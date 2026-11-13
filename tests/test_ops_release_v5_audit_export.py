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


def _seed(*, owner: str, session_id: str, tool_name: str, content: str, external_untrusted: bool = False):
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
        external_untrusted_context_seen=external_untrusted,
        selected_tools=(tool_name,),
        continuation_query="continue",
        capabilities=capabilities_for_action(tool_name, content),
    )


def test_skip_external_untrusted_bulk_guard_and_filtered_audit_export(monkeypatch):
    current_user = {"name": "alice"}
    client = _build_client(monkeypatch, current_user)
    with tool_approval_store._lock:
        tool_approval_store._pending.clear()
        tool_approval_store._decision_history.clear()

    try:
        safe_item = _seed(
            owner="alice",
            session_id="sess-v5-a",
            tool_name="write_file",
            content='{"path":"report.md"}',
            external_untrusted=False,
        )
        ext_item = _seed(
            owner="alice",
            session_id="sess-v5-b",
            tool_name="send_email",
            content='{"to":"external@example.com"}',
            external_untrusted=True,
        )
        bob_item = _seed(
            owner="bob",
            session_id="sess-v5-c",
            tool_name="write_file",
            content='{"path":"bob.md"}',
            external_untrusted=False,
        )

        # Bulk approve with skip_external_untrusted=True approves safe_item and skips ext_item
        bulk_res = client.post(
            "/api/tasks/approvals/resolve-bulk",
            json={
                "approval_ids": [safe_item.approval_id, ext_item.approval_id],
                "decision": "approve",
                "skip_external_untrusted": True,
            },
        )
        assert bulk_res.status_code == 200
        bulk_body = bulk_res.json()
        assert bulk_body["resolved_count"] == 1
        assert bulk_body["resolved"][0]["approval_id"] == safe_item.approval_id
        assert bulk_body["failed"] == [
            {"approval_id": ext_item.approval_id, "reason": "external_untrusted_context"}
        ]

        # Deny ext_item for alice and bob_item for bob
        assert client.post(f"/api/tasks/approvals/{ext_item.approval_id}/resolve", json={"decision": "deny"}).status_code == 200
        current_user["name"] = "bob"
        assert client.post(f"/api/tasks/approvals/{bob_item.approval_id}/resolve", json={"decision": "deny"}).status_code == 200
        current_user["name"] = "alice"

        # Filtered history query
        filtered = client.get("/api/tasks/approvals/history", params={"tool_name": "send_email", "decision": "deny"})
        assert filtered.status_code == 200
        f_body = filtered.json()
        assert f_body["count"] == 1
        assert f_body["decisions"][0]["approval_id"] == ext_item.approval_id

        # CSV export (strictly owner-scoped)
        csv_res = client.get("/api/tasks/approvals/history/export", params={"format": "csv"})
        assert csv_res.status_code == 200
        assert "text/csv" in csv_res.headers["content-type"]
        lines = [line for line in csv_res.text.strip().splitlines() if line]
        assert lines[0] == "approval_id,session_id,tool_name,digest,decision,resolved_at"
        assert len(lines) == 3
        assert bob_item.approval_id not in csv_res.text

        # JSON export + invalid format validation
        json_res = client.get("/api/tasks/approvals/history/export", params={"format": "json", "decision": "approve"})
        assert json_res.status_code == 200
        j_body = json_res.json()
        assert j_body["count"] == 1
        assert j_body["decisions"][0]["approval_id"] == safe_item.approval_id

        bad_res = client.get("/api/tasks/approvals/history/export", params={"format": "xml"})
        assert bad_res.status_code == 400
    finally:
        with tool_approval_store._lock:
            tool_approval_store._pending.clear()
            tool_approval_store._decision_history.clear()
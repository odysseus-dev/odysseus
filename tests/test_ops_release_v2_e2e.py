from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.diagnostics_routes as dr
import routes.task.task_routes as tr
import src.agent_runtime.effect_log as el
from routes.diagnostics_routes import setup_diagnostics_routes
from routes.task.task_routes import setup_task_routes
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.effects import (
    ExecutionOutcome,
    Impact,
    OperationRef,
    ProducerFacts,
    ResourceKind,
    ResourceRef,
)
from src.tool_approvals import tool_approval_store
from src.tool_capabilities import capabilities_for_action


class _DummyScheduler:
    pass


def test_ops_release_v2_end_to_end_drilldown_and_approval_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)
    monkeypatch.setattr(dr, "require_admin", lambda request: None)
    monkeypatch.setattr(tr, "get_current_user", lambda request: "alice", raising=False)

    run_id = "2" * 32
    log = EffectLog(run_id, directory=tmp_path)
    target = ResourceRef(
        kind=ResourceKind.EXTERNAL,
        role="url",
        location=("external", "https://example.com/report"),
        incarnation="",
        snapshot_sha256="d" * 64,
    )
    claim1 = log.claim(
        effect_id="eff-v2-1",
        action_id="act-v2-1",
        operation=OperationRef("write_file", "", "e" * 64),
        impact_scope=(target,),
        external=False,
    )
    log.outcome(
        effect_id=claim1.effect_id,
        execution_id="exec-v2-1",
        execution=ExecutionOutcome.REPORTED_SUCCESS,
        impact=Impact.CHANGED,
        facts=ProducerFacts(mutation_attempted=True),
    )
    # Unsettled second claim triggers reconciliation flag in v2 drill-down
    log.claim(
        effect_id="eff-v2-2",
        action_id="act-v2-2",
        operation=OperationRef("web_fetch", "", "f" * 64),
        impact_scope=(target,),
        external=True,
    )

    pending = tool_approval_store.create(
        owner="alice",
        session_id="sess-v2-e2e",
        origin_run_id=run_id,
        tool_name="write_file",
        content='{"path":"report.md"}',
        workspace="",
        document_id="",
        document_version=None,
        document_digest="",
        external_untrusted_context_seen=True,
        selected_tools=("write_file",),
        continuation_query="write report",
        capabilities=capabilities_for_action("write_file", '{"path":"report.md"}'),
    )

    app = FastAPI()
    app.include_router(setup_task_routes(_DummyScheduler()))
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)

    try:
        # 1. Admin lists recent effect runs and drills into the specific run_id
        list_res = client.get("/api/diagnostics/effects?limit=10")
        assert list_res.status_code == 200
        assert list_res.json()["runs"][0]["run_id"] == run_id

        detail_res = client.get(f"/api/diagnostics/effects/{run_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["run_id"] == run_id
        assert len(detail["records"]) == 3
        assert detail["reconciliation"]["unsettled_claims"] == ["eff-v2-2"]

        # 2. Owner sees pending approval with expires_at TTL metadata and resolves it once
        pending_res = client.get("/api/tasks/approvals/pending")
        assert pending_res.status_code == 200
        approvals = pending_res.json()["approvals"]
        matching = [a for a in approvals if a["approval_id"] == pending.approval_id]
        assert len(matching) == 1
        assert matching[0]["expires_at"] > 0

        resolve_res = client.post(
            f"/api/tasks/approvals/{pending.approval_id}/resolve",
            json={"decision": "approve_task"},
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json()["ok"] is True

        after_res = client.get("/api/tasks/approvals/pending").json()["approvals"]
        assert not any(a["approval_id"] == pending.approval_id for a in after_res)
    finally:
        tool_approval_store.consume(pending.approval_id, decision="deny", owner="alice", session_id="sess-v2-e2e")
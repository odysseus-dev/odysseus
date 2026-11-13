from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.diagnostics_routes import setup_diagnostics_routes
from routes.task.task_routes import setup_task_routes
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.effects import ExecutionOutcome, Impact, OperationRef, ProducerFacts, ResourceKind, ResourceRef
from src.tool_approvals import tool_approval_store
from src.tool_capabilities import capabilities_for_action


class _DummyScheduler:
    pass


def test_ops_release_v1_end_to_end_lifecycle(monkeypatch, tmp_path):
    import routes.diagnostics_routes as dr
    import routes.task.task_routes as tr
    import src.agent_runtime.effect_log as el
    import src.service_health as sh

    active_user = {"name": "alice"}
    monkeypatch.setattr(tr, "get_current_user", lambda request: active_user["name"], raising=False)
    monkeypatch.setattr(dr, "require_admin", lambda request: None)
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)

    async def _fake_health(rag_manager=None, memory_vector=None):
        return {
            "overall": "ok",
            "timestamp": "2026-11-13T10:33:00+00:00",
            "services": [
                {"name": "chromadb", "status": "ok", "detail": "ready", "meta": {}},
                {"name": "providers", "status": "ok", "detail": "1/1 reachable", "meta": {}},
            ],
        }

    monkeypatch.setattr(sh, "collect_service_health", _fake_health)

    app = FastAPI()
    app.include_router(setup_task_routes(_DummyScheduler()))
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)

    pending = tool_approval_store.create(
        owner="alice",
        session_id="sess-e2e-1",
        origin_run_id="b" * 32,
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
    try:
        summary_before = client.get("/api/diagnostics/ops-summary").json()
        assert summary_before["overall"] == "ok"
        assert summary_before["ops"]["approvals"]["pending_total"] >= 1

        active_user["name"] = "bob"
        assert client.get("/api/tasks/approvals/pending").json()["count"] == 0
        assert client.post(
            f"/api/tasks/approvals/{pending.approval_id}/resolve",
            json={"decision": "approve_task"},
        ).status_code == 404

        active_user["name"] = "alice"
        resolve_res = client.post(
            f"/api/tasks/approvals/{pending.approval_id}/resolve",
            json={"decision": "approve_task"},
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json()["ok"] is True

        run_id = "c" * 32
        log = EffectLog(run_id, directory=tmp_path)
        target = ResourceRef(
            kind=ResourceKind.EXTERNAL,
            role="url",
            location=("external", "https://example.com/report"),
            incarnation="",
            snapshot_sha256="d" * 64,
        )
        claim = log.claim(
            effect_id="eff-e2e-1",
            action_id="act-e2e-1",
            operation=OperationRef("write_file", "", "e" * 64),
            impact_scope=(target,),
            external=False,
        )
        log.outcome(
            effect_id=claim.effect_id,
            execution_id="exec-e2e-1",
            execution=ExecutionOutcome.REPORTED_SUCCESS,
            impact=Impact.CHANGED,
            facts=ProducerFacts(mutation_attempted=True),
        )

        effects_body = client.get("/api/diagnostics/effects?limit=10").json()
        assert effects_body["count"] == 1
        assert effects_body["runs"][0]["run_id"] == run_id
        assert effects_body["runs"][0]["claims"] == 1
        assert effects_body["runs"][0]["outcomes"] == 1
        assert effects_body["runs"][0]["mutation_attempted"] is True

        summary_after = client.get("/api/diagnostics/ops-summary").json()
        assert summary_after["ops"]["effects"]["recent_runs"] == 1
        assert summary_after["ops"]["effects"]["mutation_attempted_runs"] == 1
    finally:
        tool_approval_store.consume(
            pending.approval_id,
            decision="deny",
            owner="alice",
            session_id="sess-e2e-1",
        )

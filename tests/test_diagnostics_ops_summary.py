from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.diagnostics_routes import setup_diagnostics_routes
from src.tool_approvals import tool_approval_store
from src.tool_capabilities import capabilities_for_action


def test_ops_summary_requires_admin():
    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)
    resp = client.get("/api/diagnostics/ops-summary")
    assert resp.status_code in (401, 403)


def test_ops_summary_aggregates_services_approvals_and_effects(monkeypatch, tmp_path):
    import routes.diagnostics_routes as dr
    import src.service_health as sh
    import src.agent_runtime.effect_log as el

    monkeypatch.setattr(dr, "require_admin", lambda request: None)
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)

    async def _fake_health(rag_manager=None, memory_vector=None):
        return {
            "overall": "ok",
            "timestamp": "2026-11-13T10:30:00+00:00",
            "services": [{"name": "chromadb", "status": "ok", "detail": "ready", "meta": {}}],
        }

    monkeypatch.setattr(sh, "collect_service_health", _fake_health)

    pending = tool_approval_store.create(
        owner="alice",
        session_id="sess-ops-1",
        origin_run_id="run-ops-1",
        tool_name="write_file",
        content='{"path":"ops.txt"}',
        workspace="",
        document_id="",
        document_version=None,
        document_digest="",
        external_untrusted_context_seen=False,
        selected_tools=("write_file",),
        continuation_query="write ops",
        capabilities=capabilities_for_action("write_file", '{"path":"ops.txt"}'),
    )
    try:
        app = FastAPI()
        app.include_router(setup_diagnostics_routes(None, False, None))
        client = TestClient(app)

        resp = client.get("/api/diagnostics/ops-summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["overall"] == "ok"
        assert body["services"][0]["name"] == "chromadb"
        assert body["ops"]["approvals"]["pending_total"] >= 1
        assert "tasks" in body["ops"]
        assert body["ops"]["effects"]["recent_runs"] == 0
    finally:
        tool_approval_store.consume(
            pending.approval_id,
            decision="deny",
            owner="alice",
            session_id="sess-ops-1",
        )

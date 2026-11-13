import pathlib
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.diagnostics_routes as dr
import src.agent_runtime.effect_log as el
import src.service_health as sh
from routes.diagnostics_routes import setup_diagnostics_routes
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.effects import ExecutionOutcome, Impact, OperationRef, ProducerFacts, ResourceKind, ResourceRef


def test_ops_release_v2_ui_hooks_and_per_run_reconciliation(monkeypatch, tmp_path):
    repo = pathlib.Path(__file__).resolve().parents[1]
    index_html = (repo / "static" / "index.html").read_text(encoding="utf-8")
    admin_js = (repo / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    tasks_js = (repo / "static" / "js" / "tasks.js").read_text(encoding="utf-8")

    # Verify UI hooks across index.html, admin.js, and tasks.js
    assert 'id="admin-ops-runtime-panel"' in index_html
    assert "loadAdminOpsPanel" in admin_js
    assert "/api/diagnostics/effects/${encodeURIComponent(runId)}" in admin_js
    assert "_startApprovalsPolling" in tasks_js
    assert "_formatApprovalTtl" in tasks_js

    # Verify backend ops-summary + per-run drill-down lifecycle
    monkeypatch.setattr(dr, "require_admin", lambda _request: None)
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)

    async def _fake_health(_rag=None, _mem=None):
        return {
            "overall": "ok",
            "timestamp": "2026-11-13T10:00:00+00:00",
            "services": {"searxng": {"status": "ok"}},
        }

    monkeypatch.setattr(sh, "collect_service_health", _fake_health)

    run_id = "1" * 32
    log = EffectLog(run_id, directory=tmp_path)
    target = ResourceRef(
        kind=ResourceKind.EXTERNAL,
        role="endpoint",
        location=("external", "https://service.example.com/hook"),
        incarnation="",
        snapshot_sha256="2" * 64,
    )
    log.claim(
        effect_id="eff-v2-1",
        action_id="act-v2-1",
        operation=OperationRef("http_post", "send", "3" * 64),
        impact_scope=(target,),
        external=True,
    )
    log.outcome(
        effect_id="eff-v2-1",
        execution_id="exec-v2-1",
        execution=ExecutionOutcome.INTERRUPTED,
        impact=Impact.POSSIBLE,
        facts=ProducerFacts(mutation_attempted=True),
    )

    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)

    summary = client.get("/api/diagnostics/ops-summary").json()
    assert summary["overall"] == "ok"
    assert summary["ops"]["effects"]["recent_runs"] == 1
    assert summary["ops"]["effects"]["external_claim_runs"] == 1
    assert summary["ops"]["effects"]["mutation_attempted_runs"] == 1

    detail = client.get(f"/api/diagnostics/effects/{run_id}").json()
    assert detail["status"] == "success"
    assert detail["reconciliation"]["needs_attention"] is True
    assert detail["reconciliation"]["interrupted_claims"] == ["eff-v2-1"]

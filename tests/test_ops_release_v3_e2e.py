from fastapi import FastAPI
from fastapi.testclient import TestClient
import routes.diagnostics_routes as dr
import src.agent_runtime.effect_log as el
from routes.diagnostics_routes import setup_diagnostics_routes
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.effects import ExecutionOutcome, Impact, OperationRef, ProducerFacts, ResourceKind, ResourceRef


def test_ops_release_v3_end_to_end_unsettled_triage_and_reconcile(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)
    monkeypatch.setattr(dr, "require_admin", lambda request: None)
    target = ResourceRef(kind=ResourceKind.EXTERNAL, role="url", location=("external", "https://example.com/ops-v3"), incarnation="", snapshot_sha256="a" * 64)
    clean_run, orphan_run = "a" * 32, "b" * 32
    lc = EffectLog(clean_run, directory=tmp_path)
    c = lc.claim(effect_id="eff-v3-clean", action_id="act-v3-1", operation=OperationRef("web_fetch", "", "b" * 64), impact_scope=(target,), external=True)
    lc.outcome(effect_id=c.effect_id, execution_id="exec-v3-1", execution=ExecutionOutcome.REPORTED_SUCCESS, impact=Impact.POSSIBLE, facts=ProducerFacts(mutation_attempted=False))
    lo = EffectLog(orphan_run, directory=tmp_path)
    lo.claim(effect_id="eff-v3-orphan", action_id="act-v3-2", operation=OperationRef("write_file", "", "c" * 64), impact_scope=(target,), external=False)
    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)
    runs = client.get("/api/diagnostics/effects", params=dict(limit=10, unsettled_only=True)).json()["runs"]
    assert [r["run_id"] for r in runs] == [orphan_run] and runs[0]["unsettled_claims"] == 1
    rec = client.post(f"/api/diagnostics/effects/{orphan_run}/reconcile", json=dict(action="interrupted", note="confirmed worker crash"))
    assert rec.status_code == 200 and rec.json()["reconciled_count"] == 1 and rec.json()["reconciliation"]["unsettled_claims"] == []
    assert client.get("/api/diagnostics/effects", params=dict(limit=10, unsettled_only=True)).json()["count"] == 0

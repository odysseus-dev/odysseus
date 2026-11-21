from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.diagnostics_routes as dr
import src.agent_runtime.effect_log as el
from routes.diagnostics_routes import setup_diagnostics_routes
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.effects import (
    ExecutionOutcome,
    Impact,
    OperationRef,
    ProducerFacts,
    ResourceKind,
    ResourceRef,
)


def _make_client(monkeypatch, tmp_path, *, is_admin: bool = True) -> TestClient:
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)
    if is_admin:
        monkeypatch.setattr(dr, "require_admin", lambda request: None)
    else:
        def _deny(request):
            raise HTTPException(status_code=403, detail="Admin required")
        monkeypatch.setattr(dr, "require_admin", _deny)

    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    return TestClient(app)


def test_effects_unsettled_only_filter_and_reconcile_lifecycle(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, is_admin=True)
    target = ResourceRef(
        kind=ResourceKind.EXTERNAL,
        role="url",
        location=("external", "https://example.com/item"),
        incarnation="",
        snapshot_sha256="a" * 64,
    )

    settled_run = "1" * 32
    log1 = EffectLog(settled_run, directory=tmp_path)
    c1 = log1.claim(
        effect_id="eff-settled-1",
        action_id="act-1",
        operation=OperationRef("web_fetch", "", "b" * 64),
        impact_scope=(target,),
        external=True,
    )
    log1.outcome(
        effect_id=c1.effect_id,
        execution_id="exec-1",
        execution=ExecutionOutcome.REPORTED_SUCCESS,
        impact=Impact.POSSIBLE,
        facts=ProducerFacts(mutation_attempted=False),
    )

    unsettled_run = "2" * 32
    log2 = EffectLog(unsettled_run, directory=tmp_path)
    log2.claim(
        effect_id="eff-orphan-1",
        action_id="act-2",
        operation=OperationRef("write_file", "", "c" * 64),
        impact_scope=(target,),
        external=False,
    )

    filtered = client.get("/api/diagnostics/effects?unsettled_only=true")
    assert filtered.status_code == 200
    body = filtered.json()
    assert body["count"] == 1
    assert body["runs"][0]["run_id"] == unsettled_run
    assert body["runs"][0]["unsettled_claims"] == 1

    rec_res = client.post(
        f"/api/diagnostics/effects/{unsettled_run}/reconcile",
        json={"action": "cancelled", "note": "operator aborted"},
    )
    assert rec_res.status_code == 200
    rec = rec_res.json()
    assert rec["reconciled_effect_ids"] == ["eff-orphan-1"]
    assert rec["reconciled_count"] == 1
    assert rec["reconciliation"]["unsettled_claims"] == []

    # Second reconcile is idempotent (0 remaining unsettled claims)
    rec_again = client.post(f"/api/diagnostics/effects/{unsettled_run}/reconcile", json={})
    assert rec_again.status_code == 200
    assert rec_again.json()["reconciled_count"] == 0

    # Filtered list now returns 0 unsettled runs
    after = client.get("/api/diagnostics/effects?unsettled_only=true").json()
    assert after["count"] == 0


def test_reconcile_enforces_admin_and_validates_inputs(monkeypatch, tmp_path):
    non_admin = _make_client(monkeypatch, tmp_path, is_admin=False)
    res_403 = non_admin.post(f"/api/diagnostics/effects/{'3' * 32}/reconcile", json={})
    assert res_403.status_code == 403

    admin = _make_client(monkeypatch, tmp_path, is_admin=True)
    assert admin.post("/api/diagnostics/effects/bad-id/reconcile", json={}).status_code == 400
    assert admin.post(f"/api/diagnostics/effects/{'3' * 32}/reconcile", json={}).status_code == 404
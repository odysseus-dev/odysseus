from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.diagnostics_routes import setup_diagnostics_routes
from src.agent_runtime.effect_log import EffectLog, list_recent_effect_runs
from src.agent_runtime.effects import OperationRef, ResourceKind, ResourceRef


def test_list_recent_effect_runs_handles_empty_or_missing_dir(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list_recent_effect_runs(directory=missing) == []


def test_diagnostics_effects_route_returns_read_only_audit_list(monkeypatch, tmp_path):
    import routes.diagnostics_routes as dr
    import src.agent_runtime.effect_log as el

    monkeypatch.setattr(dr, "require_admin", lambda request: None)
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)

    run_id = "a" * 32
    log = EffectLog(run_id, directory=tmp_path)
    target = ResourceRef(
        kind=ResourceKind.EXTERNAL,
        role="url",
        location=("external", "https://example.com"),
        incarnation="",
        snapshot_sha256="c" * 64,
    )
    log.claim(
        effect_id="eff-1",
        action_id="act-1",
        operation=OperationRef("web_fetch", "", "b" * 64),
        impact_scope=(target,),
        external=True,
    )

    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)

    resp = client.get("/api/diagnostics/effects?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["count"] == 1
    assert body["runs"][0]["run_id"] == run_id
    assert body["runs"][0]["claims"] == 1
    assert body["runs"][0]["external_claims"] == 1
    assert body["runs"][0]["tools"] == ["web_fetch"]
    assert body["runs"][0]["corrupt"] is False


def test_diagnostics_effect_run_detail_enforces_admin_and_run_id_validation(monkeypatch, tmp_path):
    from fastapi import HTTPException
    import routes.diagnostics_routes as dr
    import src.agent_runtime.effect_log as el

    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)

    def _deny(_request):
        raise HTTPException(status_code=403, detail="Admin required")

    monkeypatch.setattr(dr, "require_admin", _deny)
    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)

    assert client.get(f"/api/diagnostics/effects/{'a' * 32}").status_code == 403

    monkeypatch.setattr(dr, "require_admin", lambda _request: None)
    assert client.get("/api/diagnostics/effects/not-a-valid-hex-id").status_code == 400
    assert client.get("/api/diagnostics/effects/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert client.get(f"/api/diagnostics/effects/{'b' * 32}").status_code == 404


def test_diagnostics_effect_run_detail_returns_records_and_reconciliation_flags(monkeypatch, tmp_path):
    import routes.diagnostics_routes as dr
    import src.agent_runtime.effect_log as el
    from src.agent_runtime.effects import ExecutionOutcome, Impact, ProducerFacts

    monkeypatch.setattr(dr, "require_admin", lambda _request: None)
    monkeypatch.setattr(el, "effects_dir", lambda: tmp_path)

    run_id = "c" * 32
    log = EffectLog(run_id, directory=tmp_path)
    target = ResourceRef(
        kind=ResourceKind.EXTERNAL,
        role="url",
        location=("external", "https://api.example.com/v1"),
        incarnation="",
        snapshot_sha256="d" * 64,
    )
    log.claim(
        effect_id="eff-1",
        action_id="act-1",
        operation=OperationRef("web_fetch", "post", "e" * 64),
        impact_scope=(target,),
        external=True,
    )
    log.outcome(
        effect_id="eff-1",
        execution_id="exec-1",
        execution=ExecutionOutcome.INTERRUPTED,
        impact=Impact.POSSIBLE,
        facts=ProducerFacts(mutation_attempted=True),
    )
    log.claim(
        effect_id="eff-2",
        action_id="act-2",
        operation=OperationRef("file_write", "", "f" * 64),
        impact_scope=(),
        external=False,
    )

    app = FastAPI()
    app.include_router(setup_diagnostics_routes(None, False, None))
    client = TestClient(app)

    resp = client.get(f"/api/diagnostics/effects/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["run_id"] == run_id
    assert body["corrupt"] is False
    assert body["counts"] == {"claims": 2, "outcomes": 1, "observations": 0}
    assert body["reconciliation"]["interrupted_claims"] == ["eff-1"]
    assert body["reconciliation"]["unsettled_claims"] == ["eff-2"]
    assert body["reconciliation"]["needs_attention"] is True
    assert [r["kind"] for r in body["records"]] == ["claim", "outcome", "claim"]

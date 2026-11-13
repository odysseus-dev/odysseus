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

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.diagnostics_routes as diag_routes
from routes.diagnostics_routes import audit_administrative_security, setup_diagnostics_routes


def test_audit_administrative_security_flags_boundary_risks():
    report = audit_administrative_security(
        auth_is_disabled=True,
        admin_user_count=0,
        total_user_count=1,
        api_tokens=[
            {"id": "tok-1", "revoked": False, "expires_at": None, "scopes": ["*"]},
            {"id": "tok-2", "revoked": True, "expires_at": None, "scopes": ["*"]},
        ],
        mcp_servers=[
            {"name": "local-fs", "transport": "stdio", "enabled": True},
            {"name": "remote-http", "transport": "http", "enabled": True},
        ],
        webhooks=[
            {"id": "wh-1", "enabled": True, "secret": ""},
            {"id": "wh-2", "enabled": True, "secret": "s3cr3t"},
        ],
        admin_only_task_actions=["action_run_local", "ssh_command"],
        shell_tool_enabled=True,
    )

    assert report["ok"] is False
    assert report["summary"]["high_findings"] == 1
    assert report["summary"]["medium_findings"] == 3
    codes = {f["code"] for f in report["findings"]}
    assert codes == {
        "auth_disabled",
        "non_expiring_api_tokens",
        "broad_scope_api_tokens",
        "stdio_mcp_servers_active",
        "unsigned_webhooks",
    }
    assert report["boundaries"]["delegated_tokens"]["active_count"] == 1
    assert report["boundaries"]["integrations"]["mcp_stdio_count"] == 1
    assert report["boundaries"]["integrations"]["webhooks_unsigned_count"] == 1


def test_audit_administrative_security_clean_posture():
    report = audit_administrative_security(
        auth_is_disabled=False,
        admin_user_count=1,
        total_user_count=3,
        api_tokens=[
            {"id": "tok-ok", "revoked": False, "expires_at": "2026-12-31T00:00:00Z", "scopes": ["chat"]},
        ],
        mcp_servers=[{"name": "remote-api", "transport": "http", "enabled": True}],
        webhooks=[{"id": "wh-ok", "enabled": True, "secret": "whsec_123"}],
        admin_only_task_actions=["action_run_local"],
        shell_tool_enabled=False,
    )

    assert report["ok"] is True
    assert report["summary"]["total_findings"] == 0
    assert report["findings"] == []


def test_security_audit_endpoint(monkeypatch):
    monkeypatch.setattr(diag_routes, "require_admin", lambda req: None)
    monkeypatch.setattr(
        diag_routes,
        "collect_administrative_security_audit",
        lambda: {
            "ok": True,
            "summary": {"high_findings": 0, "medium_findings": 0, "total_findings": 0},
            "boundaries": {},
            "findings": [],
        },
    )

    app = FastAPI()
    app.include_router(setup_diagnostics_routes(MagicMock(), True, MagicMock()))
    client = TestClient(app)

    resp = client.get("/api/diagnostics/security-audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["summary"]["total_findings"] == 0

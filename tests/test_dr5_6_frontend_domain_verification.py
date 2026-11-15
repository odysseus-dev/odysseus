"""DR-5.6 Frontend Domain Verification UI & API Contract Tests (15 tests)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.research.research_routes import setup_research_routes
from src.research_handler import ResearchHandler


def test_F1_panel_contains_opt_in_domain_verifiers_checkbox_unchecked_by_default():
    panel_js = Path("static/js/research/panel.js").read_text(encoding="utf-8")
    assert 'id="research-domain-verifiers"' in panel_js
    assert 'checked' not in panel_js.split('id="research-domain-verifiers"')[1].split(">")[0]


def test_F2_panel_reads_persists_and_restores_enable_domain_verifiers():
    panel_js = Path("static/js/research/panel.js").read_text(encoding="utf-8")
    assert "enable_domain_verifiers: Boolean(document.getElementById('research-domain-verifiers')?.checked)" in panel_js
    assert "enable_domain_verifiers: document.getElementById('research-domain-verifiers')?.checked ? true : undefined" in panel_js
    assert "dvChk.checked = Boolean(saved.enable_domain_verifiers)" in panel_js


def test_F3_jobs_forwards_settings_and_hydrates_domain_verification_on_status_and_peek():
    jobs_js = Path("static/js/research/jobs.js").read_text(encoding="utf-8")
    assert "enable_domain_verifiers: Boolean(settings?.enable_domain_verifiers)" in jobs_js
    assert "if (d.domain_verification) job.domain_verification = d.domain_verification;" in jobs_js
    assert jobs_js.count("if (d.domain_verification) job.domain_verification = d.domain_verification;") >= 2


def test_F4_panel_ensures_and_renders_domain_verification_telemetry():
    panel_js = Path("static/js/research/panel.js").read_text(encoding="utf-8")
    assert "function _renderDomainVerification(dv, verificationStatus)" in panel_js
    assert "_renderDomainVerification(job.domain_verification, job.verification_status)" in panel_js
    assert "if (d.domain_verification) job.domain_verification = d.domain_verification;" in panel_js


def test_F5_panel_renderer_escapes_all_untrusted_telemetry_fields():
    panel_js = Path("static/js/research/panel.js").read_text(encoding="utf-8")
    fn_body = panel_js.split("function _renderDomainVerification(dv, verificationStatus)")[1]
    assert "_esc(String(verificationStatus || dv.verification_status" in fn_body
    assert "_esc(String(a.domain" in fn_body
    assert "_esc(String(a.claim_assessment" in fn_body
    assert "_esc(String(a.claim_text" in fn_body
    assert "_esc(String(a.plugin_name" in fn_body
    assert "_esc(missingDoms.join(', '))" in fn_body


def test_F6_panel_renderer_surfaces_conflicts_missing_domains_and_plugin_faults():
    panel_js = Path("static/js/research/panel.js").read_text(encoding="utf-8")
    fn_body = panel_js.split("function _renderDomainVerification(dv, verificationStatus)")[1]
    assert "Preserved domain conflicts:" in fn_body
    assert "Missing domain verifier coverage:" in fn_body
    assert "Domain verifier plugin faults (fail-closed):" in fn_body


def test_F7_css_contains_domain_verification_classes():
    css = Path("static/css/17-research.css").read_text(encoding="utf-8")
    for cls in (
        ".research-setting-toggle",
        ".research-domain-verification",
        ".research-dv-header",
        ".research-dv-status",
        ".research-dv-list",
        ".research-dv-badge",
        ".research-dv-alert",
    ):
        assert cls in css


def test_F8_js_files_have_balanced_braces_and_parentheses():
    for path_str in ("static/js/research/jobs.js", "static/js/research/panel.js"):
        src = Path(path_str).read_text(encoding="utf-8")
        assert src.count("{") == src.count("}"), f"Unbalanced braces in {path_str}"
        assert src.count("(") == src.count(")"), f"Unbalanced parens in {path_str}"


def _build_test_client():
    app = FastAPI()
    handler = ResearchHandler()
    app.include_router(setup_research_routes(handler))
    return TestClient(app), handler


def test_F9_api_roundtrip_exposes_exact_telemetry_keys_consumed_by_frontend(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    client, handler = _build_test_client()
    handler._active_tasks["rp-f9"] = {
        "status": "done",
        "progress": {},
        "query": "Verify Acme FY2025 revenue",
        "started_at": 100.0,
        "owner": "alice",
        "result": "Final report body",
        "sources": [{"url": "https://sec.gov/10k", "title": "10-K"}],
        "verification_status": "completed_with_gaps",
        "verification_summary": {
            "verification_status": "completed_with_gaps",
            "claim_assessments": [
                {
                    "claim_text": "Revenue was $9.5B.",
                    "domain": "financial",
                    "plugin_name": "financial_verifier",
                    "claim_assessment": "SUPPORTED",
                }
            ],
            "conflict_records": [{"normalized_claim_key": "rev"}],
            "missing_domain_plugin": True,
            "missing_domain_domains": ["geospatial"],
            "plugin_errors": [{"plugin_name": "bad_plugin", "error_type": "RuntimeError"}],
        },
    }

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        status_resp = client.get("/api/research/status/rp-f9")
        assert status_resp.status_code == 200
        st_dv = status_resp.json()["domain_verification"]
        assert len(st_dv["claim_assessments"]) == 1
        assert len(st_dv["conflict_records"]) == 1
        assert st_dv["missing_domain_plugin"] is True
        assert st_dv["missing_domain_domains"] == ["geospatial"]
        assert len(st_dv["plugin_errors"]) == 1

        peek_resp = client.post("/api/research/result-peek/rp-f9")
        assert peek_resp.status_code == 200
        pk_dv = peek_resp.json()["domain_verification"]
        assert pk_dv == st_dv


def test_F10_api_default_session_without_flag_omits_domain_verification_on_status_and_peek(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    client, handler = _build_test_client()
    handler._active_tasks["rp-f10"] = {
        "status": "done",
        "progress": {},
        "query": "Standard query",
        "started_at": 100.0,
        "owner": "alice",
        "result": "Standard report",
        "verification_status": "verified",
        "verification_summary": {"verification_status": "verified"},
    }

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        status_json = client.get("/api/research/status/rp-f10").json()
        peek_json = client.post("/api/research/result-peek/rp-f10").json()
        assert "domain_verification" not in status_json
        assert "domain_verification" not in peek_json

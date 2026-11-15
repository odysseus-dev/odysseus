"""DR-5.5 Service & API Telemetry Exposure Tests (18 tests: R1-R8, A1-A10)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.research.research_routes import setup_research_routes
from src.research_handler import ResearchHandler


def _make_sample_verification_summary(*, enabled: bool = True) -> dict:
    base = {
        "status": "completed_with_gaps" if enabled else "verified",
        "verified_fact_count": 1,
        "corroborated_fact_count": 1,
        "single_source_claim_count": 0,
        "conflicting_evidence_count": 0,
        "unsupported_claim_count": 0,
        "unverified_citation_count": 0,
    }
    if enabled:
        base.update(
            {
                "claim_assessments": [
                    {
                        "claim_text": "Acme Corp FY2025 revenue was $9.5 billion.",
                        "domain": "financial",
                        "plugin_name": "financial_verifier",
                        "claim_assessment": "CONTESTED",
                        "publication_status": "PEER_REVIEWED",
                        "legal_currency": "CURRENT",
                        "source_dependence": "INDEPENDENT",
                        "evidence_classification": "CONFLICTING_EVIDENCE",
                    }
                ],
                "conflict_records": [
                    {
                        "normalized_claim_key": "acme corp fy2025 revenue",
                        "domains_involved": ["financial"],
                        "competing_states": ["CONTESTED", "SUPPORTED"],
                    }
                ],
                "missing_domain_plugin": True,
                "missing_domain_domains": ["historical"],
                "plugin_errors": [
                    {
                        "plugin_name": "custom_plugin",
                        "domain": "custom",
                        "status": "PLUGIN_FAILED_CLOSED",
                        "error_type": "RuntimeError",
                    }
                ],
                "routed_claims": [{"claim_text": "Acme Corp FY2025 revenue was $9.5 billion.", "matched_domains": ["financial"]}],
            }
        )
    return base


# ==============================================================================
# R1 - R8: HANDLER OPT-IN FLAG & TELEMETRY EXTRACTION INVARIANTS
# ==============================================================================


def test_R1_start_research_defaults_enable_domain_verifiers_to_false():
    handler = ResearchHandler()
    captured = {}

    async def _fake_call(query, llm_endpoint, llm_model, **kwargs):
        captured.update(kwargs)
        return "Done"

    async def _run():
        with patch.object(handler, "call_research_service", side_effect=_fake_call), patch.object(handler, "_save_result"):
            handler.start_research("rp-r1", "Test query", "http://localhost:11434", "qwen2.5:14b")
            await handler._active_tasks["rp-r1"]["task"]

    asyncio.run(_run())
    assert handler._active_tasks["rp-r1"]["enable_domain_verifiers"] is False
    assert captured["enable_domain_verifiers"] is False


def test_R2_start_research_propagates_enable_domain_verifiers_true():
    handler = ResearchHandler()
    captured = {}

    async def _fake_call(query, llm_endpoint, llm_model, **kwargs):
        captured.update(kwargs)
        return "Done"

    async def _run():
        with patch.object(handler, "call_research_service", side_effect=_fake_call), patch.object(handler, "_save_result"):
            handler.start_research(
                "rp-r2",
                "Test financial query",
                "http://localhost:11434",
                "qwen2.5:14b",
                enable_domain_verifiers=True,
            )
            await handler._active_tasks["rp-r2"]["task"]

    asyncio.run(_run())
    assert handler._active_tasks["rp-r2"]["enable_domain_verifiers"] is True
    assert captured["enable_domain_verifiers"] is True


def test_R3_call_research_service_passes_flag_to_deep_researcher_and_caches_summary():
    handler = ResearchHandler()
    init_kwargs = {}
    vs_payload = _make_sample_verification_summary(enabled=True)

    class _FakeResearcher:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)
            self.findings = []
            self.category = "general"
            self.verification_summary = vs_payload

        async def research(self, query, **kwargs):
            return "Synthesized report"

        def get_stats(self):
            return {"rounds": 1}

    task_entry = {}

    async def _run():
        with (
            patch.object(handler, "_probe_endpoint", new=AsyncMock()),
            patch.object(handler, "_probe_search_provider", new=AsyncMock()),
            patch("src.deep_research.DeepResearcher", _FakeResearcher),
        ):
            await handler.call_research_service(
                "Analyze revenue",
                "http://localhost:11434",
                "qwen2.5:14b",
                _task_entry=task_entry,
                enable_domain_verifiers=True,
            )

    asyncio.run(_run())
    assert init_kwargs["enable_domain_verifiers"] is True
    assert task_entry["verification_summary"] == vs_payload


def test_R4_get_domain_verification_telemetry_returns_none_when_disabled():
    handler = ResearchHandler()
    handler._active_tasks["rp-r4"] = {
        "status": "done",
        "verification_summary": _make_sample_verification_summary(enabled=False),
    }
    assert handler.get_verification_summary("rp-r4") is not None
    assert handler.get_domain_verification_telemetry("rp-r4") is None


def test_R5_get_domain_verification_telemetry_returns_structured_fields_when_enabled():
    handler = ResearchHandler()
    handler._active_tasks["rp-r5"] = {
        "status": "done",
        "verification_summary": _make_sample_verification_summary(enabled=True),
    }
    tel = handler.get_domain_verification_telemetry("rp-r5")
    assert tel is not None
    assert len(tel["claim_assessments"]) == 1
    assert len(tel["conflict_records"]) == 1
    assert tel["missing_domain_plugin"] is True
    assert tel["missing_domain_domains"] == ["historical"]
    assert len(tel["plugin_errors"]) == 1
    assert len(tel["routed_claims"]) == 1


def test_R6_save_result_persists_verification_summary_and_flag_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    handler = ResearchHandler()
    vs = _make_sample_verification_summary(enabled=True)
    entry = {
        "query": "Acme Q4 revenue",
        "status": "done",
        "result": "Report text",
        "started_at": 1700000000.0,
        "owner": "alice",
        "category": "general",
        "enable_domain_verifiers": True,
        "verification_summary": vs,
    }
    handler._save_result("rp-r6", entry)
    saved = json.loads((tmp_path / "rp-r6.json").read_text(encoding="utf-8"))
    assert saved["enable_domain_verifiers"] is True
    assert saved["verification_summary"] == vs

    handler._active_tasks.clear()
    tel = handler.get_domain_verification_telemetry("rp-r6")
    assert tel is not None
    assert tel["missing_domain_domains"] == ["historical"]


def test_R7_get_status_surfaces_domain_verification_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    handler = ResearchHandler()
    handler._active_tasks["rp-r7"] = {
        "status": "done",
        "progress": {},
        "query": "Test",
        "started_at": 100.0,
        "owner": "alice",
        "verification_summary": _make_sample_verification_summary(enabled=True),
    }
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        resp = client.get("/api/research/status/rp-r7")
    assert resp.status_code == 200
    body = resp.json()
    assert "domain_verification" in body
    assert body["domain_verification"]["missing_domain_domains"] == ["historical"]


def test_R8_get_status_omits_domain_verification_when_disabled():
    handler = ResearchHandler()
    handler._active_tasks["rp-r8"] = {
        "status": "done",
        "progress": {},
        "query": "Test",
        "started_at": 100.0,
        "owner": "alice",
        "verification_summary": _make_sample_verification_summary(enabled=False),
    }
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        resp = client.get("/api/research/status/rp-r8")
    assert resp.status_code == 200
    assert "domain_verification" not in resp.json()


# ==============================================================================
# A1 - A10: API ROUTE ENDPOINTS, OWNER ISOLATION & BACKWARD COMPATIBILITY
# ==============================================================================


def test_A1_post_research_start_passes_enable_domain_verifiers_true():
    handler = MagicMock()
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with (
        patch("src.auth_helpers.require_privilege", return_value="alice"),
        patch("routes.research.research_routes.resolve_endpoint", return_value=("http://localhost:11434", "qwen2.5:14b", {})),
    ):
        resp = client.post(
            "/api/research/start",
            json={"query": "Verify FDA clinical trial", "enable_domain_verifiers": True},
        )
    assert resp.status_code == 200
    assert handler.start_research.call_args.kwargs["enable_domain_verifiers"] is True


def test_A2_post_research_start_defaults_enable_domain_verifiers_false():
    handler = MagicMock()
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with (
        patch("src.auth_helpers.require_privilege", return_value="alice"),
        patch("routes.research.research_routes.resolve_endpoint", return_value=("http://localhost:11434", "qwen2.5:14b", {})),
    ):
        resp = client.post("/api/research/start", json={"query": "Standard research query"})
    assert resp.status_code == 200
    assert handler.start_research.call_args.kwargs["enable_domain_verifiers"] is False


def test_A3_post_research_result_returns_verification_summary_and_domain_verification(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    handler = ResearchHandler()
    vs = _make_sample_verification_summary(enabled=True)
    handler._active_tasks["rp-a3"] = {
        "status": "done",
        "result": "Final report body",
        "query": "Test",
        "started_at": 100.0,
        "owner": "alice",
        "verification_summary": vs,
    }
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        resp = client.post("/api/research/result/rp-a3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_summary"] == vs
    assert len(data["domain_verification"]["claim_assessments"]) == 1
    assert len(data["domain_verification"]["conflict_records"]) == 1
    assert len(data["domain_verification"]["plugin_errors"]) == 1


def test_A4_post_research_result_omits_domain_verification_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    handler = ResearchHandler()
    vs = _make_sample_verification_summary(enabled=False)
    handler._active_tasks["rp-a4"] = {
        "status": "done",
        "result": "Final report body",
        "query": "Test",
        "started_at": 100.0,
        "owner": "alice",
        "verification_summary": vs,
    }
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        resp = client.post("/api/research/result/rp-a4")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_summary"] == vs
    assert "domain_verification" not in data


def test_A5_result_peek_live_and_disk_paths_expose_domain_verification(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    monkeypatch.setattr("routes.research.research_routes._research_storage_root", lambda: tmp_path)
    handler = ResearchHandler()
    vs = _make_sample_verification_summary(enabled=True)
    handler._save_result(
        "rp-a5",
        {
            "query": "Test peek",
            "status": "done",
            "result": "Peek report",
            "started_at": 100.0,
            "owner": "alice",
            "verification_summary": vs,
        },
    )
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        resp = client.post("/api/research/result-peek/rp-a5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_summary"] == vs
    assert data["domain_verification"]["missing_domain_plugin"] is True


def test_A6_cross_owner_cannot_read_domain_verification_via_status_or_result(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    handler = ResearchHandler()
    handler._active_tasks["rp-a6"] = {
        "status": "done",
        "progress": {},
        "result": "Secret financial analysis",
        "query": "Secret",
        "started_at": 100.0,
        "owner": "alice",
        "verification_summary": _make_sample_verification_summary(enabled=True),
    }
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="mallory"):
        st_resp = client.get("/api/research/status/rp-a6")
        res_resp = client.post("/api/research/result/rp-a6")
    assert st_resp.json() == {"status": "none"}
    assert res_resp.status_code == 404


def test_A7_legacy_handler_without_telemetry_methods_still_works_cleanly():
    legacy_handler = SimpleNamespace(
        get_status=lambda sid: {"status": "done", "progress": {}, "query": "q", "started_at": 1},
        get_result=lambda sid: "ok",
        get_sources=lambda sid: [],
        get_raw_findings=lambda sid: [],
        get_analyzed_urls=lambda sid: [],
        get_source_state=lambda sid: "",
        get_source_coverage=lambda sid: {},
        get_navigation_trace=lambda sid: [],
        get_action_trace=lambda sid: [],
        get_category=lambda sid: "",
        get_mode=lambda sid: "research",
        clear_result=lambda sid: None,
        _active_tasks={"rp-a7": {"owner": "alice"}},
    )
    app = FastAPI()
    app.include_router(setup_research_routes(legacy_handler))
    client = TestClient(app)

    with patch("routes.research.research_routes.get_current_user", return_value="alice"):
        resp = client.post("/api/research/result/rp-a7")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"
    assert "domain_verification" not in resp.json()


def test_A8_malformed_verification_summary_types_fail_closed_to_none():
    assert ResearchHandler._extract_domain_verification_telemetry(None) is None
    assert ResearchHandler._extract_domain_verification_telemetry("invalid") is None
    assert ResearchHandler._extract_domain_verification_telemetry({"status": "verified"}) is None


def test_A9_extract_telemetry_normalizes_missing_optional_lists():
    tel = ResearchHandler._extract_domain_verification_telemetry({"claim_assessments": []})
    assert tel == {
        "claim_assessments": [],
        "conflict_records": [],
        "missing_domain_plugin": False,
        "missing_domain_domains": [],
        "plugin_errors": [],
        "routed_claims": [],
    }


def test_A10_non_boolean_truthy_request_flag_is_coerced_to_bool():
    handler = MagicMock()
    app = FastAPI()
    app.include_router(setup_research_routes(handler))
    client = TestClient(app)

    with (
        patch("src.auth_helpers.require_privilege", return_value="alice"),
        patch("routes.research.research_routes.resolve_endpoint", return_value=("http://localhost:11434", "qwen2.5:14b", {})),
    ):
        resp = client.post(
            "/api/research/start",
            json={"query": "Coercion check", "enable_domain_verifiers": 1},
        )
    assert resp.status_code == 200
    val = handler.start_research.call_args.kwargs["enable_domain_verifiers"]
    assert val is True and type(val) is bool

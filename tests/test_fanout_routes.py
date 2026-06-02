"""Tests for routes/fanout_routes.py and swarm_fanout.py.

Covers the fan-out subagent orchestrator: pattern listing, run triggering,
status checking, findings retrieval, and result aggregation.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
import types
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import HTTPException

# ── Imports ──────────────────────────────────────────────────────────────

from routes.fanout_routes import (
    setup_fanout_routes,
    FanoutRequest,
    _active_runs,
    _completed_runs,
    RESULTS_DIR,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _find_endpoint(router, path, method="GET"):
    """Return the endpoint callable for *method* *path* on *router*."""
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", {""}):
            return route.endpoint
    raise AssertionError(f"Route {method} {path} not found on router")


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def fanout_router(tmp_path, monkeypatch):
    """Provide a fanout router with mocked dependencies."""
    # Clear in-memory runs
    _active_runs.clear()
    _completed_runs.clear()

    # Mock RESULTS_DIR to use tmp
    results_dir = str(tmp_path / "results")
    os.makedirs(results_dir, exist_ok=True)
    monkeypatch.setattr("routes.fanout_routes.RESULTS_DIR", results_dir)

    return setup_fanout_routes()


@pytest.fixture
def mock_fanout_module(monkeypatch):
    """Mock the swarm_fanout module functions."""
    mock_patterns = {
        "code-review": {
            "description": "Parallel code review",
            "agents": ["code-reviewer"],
            "max_workers": 4,
        },
        "security-audit": {
            "description": "Security audit",
            "agents": ["security-auditor"],
            "max_workers": 4,
        },
    }

    mock_agents = {
        "code-reviewer": {
            "name": "Code Reviewer",
            "role": "Reviews code for bugs",
        },
        "security-auditor": {
            "name": "Security Auditor",
            "role": "Deep security analysis",
        },
    }

    # Create mock module
    mock_module = types.ModuleType("swarm_fanout")
    mock_module.FANOUT_PATTERNS = mock_patterns
    mock_module.AGENT_TEMPLATES = mock_agents
    mock_module.run_fanout = MagicMock()
    mock_module.synthesize_findings = MagicMock(return_value="Synthesis report")

    monkeypatch.setitem(sys.modules, "swarm_fanout", mock_module)

    return mock_module


# ======================================================================
# Pattern Listing
# ======================================================================

class TestFanoutPatterns:

    def test_list_patterns(self, fanout_router, mock_fanout_module):
        ep = _find_endpoint(fanout_router, "/api/fanout/patterns")
        result = asyncio.run(ep())
        assert isinstance(result, list)
        assert len(result) == 2
        assert any(p["name"] == "code-review" for p in result)

    def test_list_agents(self, fanout_router, mock_fanout_module):
        ep = _find_endpoint(fanout_router, "/api/fanout/agents")
        result = asyncio.run(ep())
        assert isinstance(result, list)
        assert len(result) == 2
        assert any(a["id"] == "code-reviewer" for a in result)


# ======================================================================
# Run Management
# ======================================================================

class TestFanoutRuns:

    def test_list_runs_empty(self, fanout_router):
        ep = _find_endpoint(fanout_router, "/api/fanout/runs")
        result = asyncio.run(ep())
        assert result == []

    def test_list_runs_with_active(self, fanout_router):
        _active_runs["run-1"] = {
            "run_id": "run-1",
            "status": "running",
            "pattern": "code-review",
            "started_at": time.time(),
        }

        ep = _find_endpoint(fanout_router, "/api/fanout/runs")
        result = asyncio.run(ep())
        assert len(result) == 1
        assert result[0]["run_id"] == "run-1"
        assert result[0]["status"] == "running"

    def test_list_runs_with_completed(self, fanout_router):
        _completed_runs["run-2"] = {
            "run_id": "run-2",
            "status": "completed",
            "pattern": "security-audit",
            "started_at": time.time() - 100,
            "elapsed": 95.5,
        }

        ep = _find_endpoint(fanout_router, "/api/fanout/runs")
        result = asyncio.run(ep())
        assert len(result) == 1
        assert result[0]["run_id"] == "run-2"
        assert result[0]["status"] == "completed"

    def test_list_runs_filter_by_status(self, fanout_router):
        _active_runs["run-1"] = {"run_id": "run-1", "status": "running", "pattern": "x", "started_at": time.time()}
        _completed_runs["run-2"] = {"run_id": "run-2", "status": "completed", "pattern": "x", "started_at": time.time()}

        ep = _find_endpoint(fanout_router, "/api/fanout/runs")
        running = asyncio.run(ep(status="running"))
        completed = asyncio.run(ep(status="completed"))
        assert len(running) == 1
        assert len(completed) == 1

    def test_get_run_active(self, fanout_router):
        _active_runs["run-1"] = {
            "run_id": "run-1",
            "status": "running",
            "pattern": "code-review",
            "started_at": time.time(),
        }

        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}")
        result = asyncio.run(ep(run_id="run-1"))
        assert result["run_id"] == "run-1"
        assert "elapsed" in result

    def test_get_run_not_found(self, fanout_router):
        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(run_id="nonexistent"))
        assert exc.value.status_code == 404

    def test_delete_run(self, fanout_router, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir, exist_ok=True)
        monkeypatch.setattr("routes.fanout_routes.RESULTS_DIR", results_dir)

        # Create a result file
        result_file = os.path.join(results_dir, "run-to-delete.json")
        with open(result_file, "w") as f:
            json.dump({"pattern": "test"}, f)

        _completed_runs["run-to-delete"] = {"run_id": "run-to-delete", "status": "completed"}

        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}", "DELETE")
        result = asyncio.run(ep(run_id="run-to-delete"))
        assert result["status"] == "deleted"
        assert not os.path.exists(result_file)
        assert "run-to-delete" not in _completed_runs


# ======================================================================
# Findings
# ======================================================================

class TestFanoutFindings:

    def test_get_findings(self, fanout_router, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir, exist_ok=True)
        monkeypatch.setattr("routes.fanout_routes.RESULTS_DIR", results_dir)

        # Create result file with findings
        result_file = os.path.join(results_dir, "run-findings.json")
        findings_data = {
            "pattern": "code-review",
            "findings": [
                {"severity": "high", "title": "SQL injection", "file": "routes/auth.py", "agent": "code-reviewer"},
                {"severity": "low", "title": "Unused import", "file": "src/utils.py", "agent": "code-reviewer"},
                {"severity": "medium", "title": "Missing validation", "file": "routes/api.py", "agent": "security-auditor"},
            ],
        }
        with open(result_file, "w") as f:
            json.dump(findings_data, f)

        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}/findings")

        # Get all findings
        result = asyncio.run(ep(run_id="run-findings"))
        assert len(result) == 3

        # Filter by severity
        result = asyncio.run(ep(run_id="run-findings", severity="high"))
        assert len(result) == 1
        assert result[0]["severity"] == "high"

        # Filter by agent
        result = asyncio.run(ep(run_id="run-findings", agent="security-auditor"))
        assert len(result) == 1

        # Filter by file
        result = asyncio.run(ep(run_id="run-findings", file="routes/auth.py"))
        assert len(result) == 1

    def test_get_findings_not_found(self, fanout_router):
        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}/findings")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(run_id="nonexistent"))
        assert exc.value.status_code == 404


# ======================================================================
# Synthesis
# ======================================================================

class TestFanoutSynthesis:

    def test_get_synthesis(self, fanout_router, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir, exist_ok=True)
        monkeypatch.setattr("routes.fanout_routes.RESULTS_DIR", results_dir)

        result_file = os.path.join(results_dir, "run-synth.json")
        with open(result_file, "w") as f:
            json.dump({"pattern": "test", "synthesis": "Fix the SQL injection first"}, f)

        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}/synthesis")
        result = asyncio.run(ep(run_id="run-synth"))
        assert result["synthesis"] == "Fix the SQL injection first"

    def test_get_synthesis_missing(self, fanout_router, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir, exist_ok=True)
        monkeypatch.setattr("routes.fanout_routes.RESULTS_DIR", results_dir)

        result_file = os.path.join(results_dir, "run-no-synth.json")
        with open(result_file, "w") as f:
            json.dump({"pattern": "test", "synthesis": ""}, f)

        ep = _find_endpoint(fanout_router, "/api/fanout/runs/{run_id}/synthesis")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep(run_id="run-no-synth"))
        assert exc.value.status_code == 404


# ======================================================================
# Stats
# ======================================================================

class TestFanoutStats:

    def test_stats_empty(self, fanout_router):
        ep = _find_endpoint(fanout_router, "/api/fanout/stats")
        result = asyncio.run(ep())
        assert result["active_runs"] == 0
        assert result["completed_runs_in_memory"] == 0
        assert result["total_findings"] == 0

    def test_stats_with_data(self, fanout_router):
        _active_runs["r1"] = {"run_id": "r1", "status": "running", "pattern": "x", "started_at": time.time()}
        _completed_runs["r2"] = {
            "run_id": "r2",
            "status": "completed",
            "pattern": "x",
            "started_at": time.time(),
            "summary": {"total_findings": 5, "findings_by_severity": {"high": 2, "low": 3}},
        }

        ep = _find_endpoint(fanout_router, "/api/fanout/stats")
        result = asyncio.run(ep())
        assert result["active_runs"] == 1
        assert result["completed_runs_in_memory"] == 1
        assert result["total_findings"] == 5
        assert result["findings_by_severity"]["high"] == 2


# ======================================================================
# swarm_fanout.py Unit Tests
# ======================================================================

class TestSwarmFanoutCore:

    def test_extract_json_plain(self):
        from swarm_fanout import _extract_json
        result = _extract_json('{"findings": []}')
        assert result == {"findings": []}

    def test_extract_json_with_markdown(self):
        from swarm_fanout import _extract_json
        result = _extract_json('```json\n{"findings": [{"severity": "high"}]}\n```')
        assert result == {"findings": [{"severity": "high"}]}

    def test_extract_json_no_json(self):
        from swarm_fanout import _extract_json
        result = _extract_json("no json here")
        assert result is None

    def test_subagent_task_creation(self):
        from swarm_fanout import SubagentTask
        task = SubagentTask(
            task_id="test-1",
            agent_id="code-reviewer",
            file_path="src/test.py",
            file_content="print('hello')",
            prompt_template="Review {file_path}: {file_content}",
        )
        assert task.task_id == "test-1"
        assert task.agent_id == "code-reviewer"

    def test_subagent_result_creation(self):
        from swarm_fanout import SubagentResult
        result = SubagentResult(
            task_id="test-1",
            agent_id="code-reviewer",
            file_path="src/test.py",
            success=True,
            findings=[{"severity": "high", "title": "test"}],
            elapsed_seconds=1.5,
        )
        assert result.success is True
        assert len(result.findings) == 1

    def test_fanout_patterns_exist(self):
        from swarm_fanout import FANOUT_PATTERNS
        assert "code-review" in FANOUT_PATTERNS
        assert "security-audit" in FANOUT_PATTERNS
        assert "full-scan" in FANOUT_PATTERNS
        assert "backend-deep" in FANOUT_PATTERNS

    def test_agent_templates_exist(self):
        from swarm_fanout import AGENT_TEMPLATES
        assert "code-reviewer" in AGENT_TEMPLATES
        assert "security-auditor" in AGENT_TEMPLATES
        assert "perf-analyst" in AGENT_TEMPLATES
        assert "frontend-auditor" in AGENT_TEMPLATES

    def test_collect_python_files(self):
        from swarm_fanout import collect_python_files
        # Should return a list (may be empty if running outside odysseus dir)
        files = collect_python_files(5)
        assert isinstance(files, list)

    def test_pattern_agents_valid(self):
        """All agents referenced in patterns should exist in templates."""
        from swarm_fanout import FANOUT_PATTERNS, AGENT_TEMPLATES
        for pattern_name, pattern in FANOUT_PATTERNS.items():
            for agent_id in pattern["agents"]:
                assert agent_id in AGENT_TEMPLATES, (
                    f"Pattern '{pattern_name}' references unknown agent '{agent_id}'"
                )

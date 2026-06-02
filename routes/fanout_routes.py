"""Tech Duinn fan-out orchestrator routes — /api/fanout/*.

Exposes the swarm fan-out subagent system as REST endpoints.
Allows triggering parallel agent runs, checking status, and retrieving results.
"""

import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Data directory
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tech_duinn")
DB_PATH = os.path.join(DATA_DIR, "tech-duinn.db")
RESULTS_DIR = os.path.join(DATA_DIR, "fanout_results")

_conn: Optional[sqlite3.Connection] = None
_initialized = False

# In-memory run tracking (resets on server restart)
_active_runs: Dict[str, Dict[str, Any]] = {}
_completed_runs: Dict[str, Dict[str, Any]] = {}


def _get_conn() -> sqlite3.Connection:
    """Lazy-init database connection."""
    global _conn, _initialized
    if _initialized and _conn:
        return _conn
    _initialized = True
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


# ── Pydantic models ──────────────────────────────────────────────────────

class FanoutRequest(BaseModel):
    pattern: str
    model: Optional[str] = None
    max_files: Optional[int] = None
    synthesize: bool = False


class FanoutStatus(BaseModel):
    run_id: str
    status: str  # "running", "completed", "failed"
    pattern: str
    started_at: float
    elapsed: Optional[float] = None
    progress: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


# ── Background runner ────────────────────────────────────────────────────

def _run_fanout_background(
    run_id: str,
    pattern: str,
    model: Optional[str],
    max_files: Optional[int],
    synthesize: bool,
):
    """Run fan-out in background thread."""
    try:
        # Import here to avoid circular imports at module level
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from swarm_fanout import run_fanout, synthesize_findings, FANOUT_PATTERNS

        _active_runs[run_id]["status"] = "running"
        _active_runs[run_id]["started_at"] = time.time()

        result = run_fanout(
            pattern,
            model=model,
            max_files=max_files,
            dry_run=False,
        )

        if synthesize and result.total_findings > 0:
            synthesis = synthesize_findings(result)
            result.synthesis = synthesis

        # Build result dict
        all_findings = []
        for r in result.results:
            all_findings.extend(r.findings)

        result_dict = {
            "pattern": result.pattern,
            "total_files": result.total_files,
            "total_findings": result.total_findings,
            "findings_by_severity": result.findings_by_severity,
            "findings_by_agent": result.findings_by_agent,
            "elapsed_seconds": result.elapsed_seconds,
            "findings": all_findings[:100],  # Limit stored findings
            "synthesis": result.synthesis if synthesize else "",
        }

        # Save to disk
        result_file = os.path.join(RESULTS_DIR, f"{run_id}.json")
        with open(result_file, "w") as f:
            json.dump(result_dict, f, indent=2)

        # Update in-memory tracking
        _active_runs.pop(run_id, None)
        _completed_runs[run_id] = {
            "run_id": run_id,
            "status": "completed",
            "pattern": pattern,
            "started_at": _active_runs.get(run_id, {}).get("started_at", time.time()),
            "elapsed": result.elapsed_seconds,
            "result_file": result_file,
            "summary": {
                "total_files": result.total_files,
                "total_findings": result.total_findings,
                "findings_by_severity": result.findings_by_severity,
            },
        }

        # Log to swarm DB
        if DB_PATH.exists():
            conn = _get_conn()
            now = time.time()
            conn.execute(
                "INSERT INTO events (topic, source, payload, timestamp) VALUES (?, ?, ?, ?)",
                ("fanout.completed", "fanout-api", json.dumps({
                    "run_id": run_id,
                    "pattern": pattern,
                    "findings": result.total_findings,
                    "elapsed": result.elapsed_seconds,
                }), now),
            )
            conn.commit()

        logger.info(f"Fan-out run {run_id} completed: {result.total_findings} findings in {result.elapsed_seconds:.1f}s")

    except Exception as e:
        logger.error(f"Fan-out run {run_id} failed: {e}")
        _active_runs.pop(run_id, None)
        _completed_runs[run_id] = {
            "run_id": run_id,
            "status": "failed",
            "pattern": pattern,
            "error": str(e),
        }


# ── Routes ────────────────────────────────────────────────────────────────

def setup_fanout_routes() -> APIRouter:
    router = APIRouter(tags=["fanout"])

    @router.get("/api/fanout/patterns")
    async def list_patterns() -> List[Dict[str, Any]]:
        """List available fan-out patterns."""
        from swarm_fanout import FANOUT_PATTERNS, AGENT_TEMPLATES
        patterns = []
        for name, info in FANOUT_PATTERNS.items():
            agents = []
            for aid in info["agents"]:
                if aid in AGENT_TEMPLATES:
                    agents.append({
                        "id": aid,
                        "name": AGENT_TEMPLATES[aid]["name"],
                        "role": AGENT_TEMPLATES[aid]["role"],
                    })
            patterns.append({
                "name": name,
                "description": info["description"],
                "agents": agents,
                "max_workers": info["max_workers"],
            })
        return patterns

    @router.get("/api/fanout/agents")
    async def list_agents() -> List[Dict[str, Any]]:
        """List available agent templates."""
        from swarm_fanout import AGENT_TEMPLATES
        agents = []
        for aid, info in AGENT_TEMPLATES.items():
            agents.append({
                "id": aid,
                "name": info["name"],
                "role": info["role"],
            })
        return agents

    @router.post("/api/fanout/run", status_code=202)
    async def start_fanout(body: FanoutRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
        """Start a fan-out run in the background."""
        from swarm_fanout import FANOUT_PATTERNS

        if body.pattern not in FANOUT_PATTERNS:
            raise HTTPException(400, f"Unknown pattern: {body.pattern}. Available: {list(FANOUT_PATTERNS.keys())}")

        run_id = f"fanout-{uuid.uuid4().hex[:12]}"

        _active_runs[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "pattern": body.pattern,
            "started_at": time.time(),
        }

        background_tasks.add_task(
            _run_fanout_background,
            run_id,
            body.pattern,
            body.model,
            body.max_files,
            body.synthesize,
        )

        return {
            "run_id": run_id,
            "status": "queued",
            "pattern": body.pattern,
            "message": f"Fan-out run started. Check status at /api/fanout/runs/{run_id}",
        }

    @router.get("/api/fanout/runs")
    async def list_runs(
        status: Optional[str] = None,
        limit: int = Query(20, ge=1, le=100),
    ) -> List[Dict[str, Any]]:
        """List fan-out runs (active + completed)."""
        runs = []

        # Active runs
        for run_id, info in _active_runs.items():
            info["elapsed"] = time.time() - info.get("started_at", time.time())
            runs.append(info)

        # Completed runs (most recent first)
        for run_id, info in sorted(_completed_runs.items(), key=lambda x: x[1].get("started_at", 0), reverse=True):
            runs.append(info)

        # Load from disk if memory is empty
        if not runs and os.path.exists(RESULTS_DIR):
            for fname in sorted(os.listdir(RESULTS_DIR), reverse=True)[:limit]:
                if fname.endswith(".json"):
                    run_id = fname.replace(".json", "")
                    result_file = os.path.join(RESULTS_DIR, fname)
                    try:
                        with open(result_file) as f:
                            data = json.load(f)
                        runs.append({
                            "run_id": run_id,
                            "status": "completed",
                            "pattern": data.get("pattern", "unknown"),
                            "summary": {
                                "total_files": data.get("total_files", 0),
                                "total_findings": data.get("total_findings", 0),
                            },
                        })
                    except Exception:
                        pass

        if status:
            runs = [r for r in runs if r.get("status") == status]

        return runs[:limit]

    @router.get("/api/fanout/runs/{run_id}")
    async def get_run(run_id: str) -> Dict[str, Any]:
        """Get details of a specific fan-out run."""
        # Check active
        if run_id in _active_runs:
            info = _active_runs[run_id].copy()
            info["elapsed"] = time.time() - info.get("started_at", time.time())
            return info

        # Check completed in memory
        if run_id in _completed_runs:
            info = _completed_runs[run_id].copy()
            # Load full results from disk
            result_file = info.get("result_file")
            if result_file and os.path.exists(result_file):
                with open(result_file) as f:
                    info["result"] = json.load(f)
            return info

        # Check disk
        result_file = os.path.join(RESULTS_DIR, f"{run_id}.json")
        if os.path.exists(result_file):
            with open(result_file) as f:
                data = json.load(f)
            return {
                "run_id": run_id,
                "status": "completed",
                "pattern": data.get("pattern", "unknown"),
                "result": data,
            }

        raise HTTPException(404, f"Run {run_id} not found")

    @router.get("/api/fanout/runs/{run_id}/findings")
    async def get_run_findings(
        run_id: str,
        severity: Optional[str] = None,
        agent: Optional[str] = None,
        file: Optional[str] = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """Get findings from a fan-out run with optional filters."""
        result_file = os.path.join(RESULTS_DIR, f"{run_id}.json")
        if not os.path.exists(result_file):
            raise HTTPException(404, f"Run {run_id} not found")

        with open(result_file) as f:
            data = json.load(f)

        findings = data.get("findings", [])

        if severity:
            findings = [f for f in findings if f.get("severity") == severity]
        if agent:
            findings = [f for f in findings if f.get("agent") == agent]
        if file:
            findings = [f for f in findings if f.get("file") == file]

        return findings[:limit]

    @router.get("/api/fanout/runs/{run_id}/synthesis")
    async def get_run_synthesis(run_id: str) -> Dict[str, str]:
        """Get the synthesis report for a completed run."""
        result_file = os.path.join(RESULTS_DIR, f"{run_id}.json")
        if not os.path.exists(result_file):
            raise HTTPException(404, f"Run {run_id} not found")

        with open(result_file) as f:
            data = json.load(f)

        synthesis = data.get("synthesis", "")
        if not synthesis:
            raise HTTPException(404, "No synthesis report available for this run")

        return {"run_id": run_id, "synthesis": synthesis}

    @router.delete("/api/fanout/runs/{run_id}")
    async def delete_run(run_id: str) -> Dict[str, str]:
        """Delete a fan-out run's results."""
        _active_runs.pop(run_id, None)
        _completed_runs.pop(run_id, None)

        result_file = os.path.join(RESULTS_DIR, f"{run_id}.json")
        if os.path.exists(result_file):
            os.remove(result_file)

        return {"status": "deleted", "run_id": run_id}

    @router.get("/api/fanout/stats")
    async def fanout_stats() -> Dict[str, Any]:
        """Get fan-out system statistics."""
        active = len(_active_runs)
        completed = len(_completed_runs)

        # Count on disk
        disk_runs = 0
        if os.path.exists(RESULTS_DIR):
            disk_runs = len([f for f in os.listdir(RESULTS_DIR) if f.endswith(".json")])

        # Aggregate findings from all completed runs
        total_findings = 0
        severity_totals = {}
        for info in _completed_runs.values():
            summary = info.get("summary", {})
            total_findings += summary.get("total_findings", 0)
            for sev, count in summary.get("findings_by_severity", {}).items():
                severity_totals[sev] = severity_totals.get(sev, 0) + count

        return {
            "active_runs": active,
            "completed_runs_in_memory": completed,
            "completed_runs_on_disk": disk_runs,
            "total_findings": total_findings,
            "findings_by_severity": severity_totals,
        }

    return router

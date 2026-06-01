"""Gnexus memory / skills / model routing helpers for JUNIPERUS090.

This module is deliberately conservative. It reads governance registries and
returns proposals. It does not write memories, mutate skills, switch models,
call connectors, or store secrets.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _json_path(*parts: str) -> Path:
    return _repo_root().joinpath(*parts)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def load_memory_routing_state() -> Dict[str, Any]:
    repo = _repo_root()
    return {
        "status": "JUNIPERUS_MEMORY_SKILLS_MODEL_ROUTING_READY",
        "route": "/gnexus/memory-routing",
        "repoRoot": str(repo),
        "routingRegistry": _load_json(_json_path("data", "gnexus", "memory-routing", "routing-registry.json"), {}),
        "skillRoutingMap": _load_json(_json_path("data", "gnexus", "memory-routing", "skill-routing-map.json"), {}),
        "modelRoutingMap": _load_json(_json_path("data", "gnexus", "memory-routing", "model-routing-map.json"), {}),
        "projectMemoryBindings": _load_json(_json_path("data", "gnexus", "memory-routing", "project-memory-bindings.json"), {}),
        "contextPolicyLedger": _load_json(_json_path("data", "gnexus", "memory-routing", "context-policy-ledger.json"), {}),
        "missionState": _load_json(_json_path("data", "gnexus", "mission-control", "memory-routing-state.json"), {}),
        "boundaries": {
            "autoMemoryWrite": False,
            "autoSkillMutation": False,
            "autoModelSwitch": False,
            "externalReads": False,
            "externalWrites": False,
            "connectorCalls": False,
            "secretsStored": False,
            "humanApprovalRequired": True,
        },
    }


def propose_route(task: str, project_id: str = "") -> Dict[str, Any]:
    """Return a safe routing proposal for a task.

    The result is advisory only. Any memory write, skill mutation, or model
    switch remains approval-gated by downstream packages.
    """
    task_l = (task or "").lower()
    state = load_memory_routing_state()

    skill_suggestions: List[str] = []
    for family in state.get("skillRoutingMap", {}).get("skillFamilies", []):
        triggers = family.get("triggers") or []
        if any(str(t).lower() in task_l for t in triggers):
            skill_suggestions.append(family.get("id"))

    model_route = "local_sensitive_context"
    if any(k in task_l for k in ("code", "patch", "architecture", "governance", "multi-file")):
        model_route = "code_architecture_reasoning"
    elif any(k in task_l for k in ("classify", "route", "summarize", "status")):
        model_route = "fast_classification"

    return {
        "status": "proposal_only",
        "projectId": project_id or "unselected",
        "task": task,
        "recommendedSkillFamilies": skill_suggestions,
        "recommendedModelRoute": model_route,
        "requiresApprovalBefore": [
            "memory_write",
            "skill_mutation",
            "external_or_paid_model_switch",
            "connector_call",
        ],
        "boundary": state["boundaries"],
    }

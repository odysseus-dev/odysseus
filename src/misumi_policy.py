"""Misumi persona context policy.

Personas select context and skill families. Odysseus remains the security
principal and enforces the resulting tool denylist before dispatch.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Mapping, Set

from src.constants import BASE_DIR


POLICY_PATH = Path(BASE_DIR) / "config" / "misumi_persona_policy.json"
VALID_APPROVALS = {"none", "plan_only", "approved_read_only", "approved_execute"}
DISPLAY_NAMES = {
    "aoteru": "Aoteru", "lelouch": "Lelouch", "kurisu": "Kurisu",
    "misato": "Misato", "jin": "Jin", "sanji": "Sanji", "l": "L",
    "ginko": "Ginko", "ichigo": "Ichigo", "giorno": "Giorno", "erwin": "Erwin",
}

TOOL_FAMILIES: Mapping[str, Set[str]] = {
    "shell": {"bash", "python"},
    "shell_write": {"bash", "python", "write_file", "edit_file"},
    "email_send": {"send_email", "reply_to_email", "bulk_email"},
    "calendar_write": {"manage_calendar"},
    "bank_write": {"bank_write", "manage_bank", "transfer_funds"},
}

READ_ONLY_APPROVAL_BLOCKS = {
    "bash", "python", "write_file", "edit_file", "create_document",
    "edit_document", "update_document", "suggest_document", "send_email",
    "reply_to_email", "bulk_email", "delete_email", "archive_email",
    "manage_calendar", "manage_contact", "manage_notes", "manage_tasks",
    "manage_memory", "manage_skills", "manage_settings", "manage_endpoints",
    "manage_mcp", "manage_webhooks", "manage_tokens", "download_model",
    "serve_model", "serve_preset", "stop_served_model", "generate_image",
    "edit_image", "trigger_research", "manage_research", "api_call", "app_api",
}


@lru_cache(maxsize=4)
def load_persona_policy(path: str = "") -> Dict[str, Dict[str, object]]:
    configured = Path(path or os.getenv("MISUMI_PERSONA_POLICY_PATH") or POLICY_PATH)
    with configured.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict) or "aoteru" not in raw:
        raise ValueError("Misumi persona policy must contain aoteru")
    return {str(name).lower(): dict(record) for name, record in raw.items() if isinstance(record, dict)}


def normalize_persona(persona: object) -> str:
    value = str(persona or "aoteru").strip().lower()
    return value if value in load_persona_policy() else "aoteru"


def persona_record(persona: object) -> Dict[str, object]:
    name = normalize_persona(persona)
    return {"id": name, "display_name": DISPLAY_NAMES.get(name, name.title()), **load_persona_policy()[name]}


def expand_tool_labels(labels: Iterable[object]) -> Set[str]:
    expanded: Set[str] = set()
    for raw in labels:
        label = str(raw or "").strip()
        if not label:
            continue
        expanded.update(TOOL_FAMILIES.get(label, {label}))
    return expanded


def persona_disabled_tools(persona: object, approval: object = "none") -> Set[str]:
    """Return the effective Misumi denylist for this request.

    Phase A task execution is read-only. Only Lelouch with explicit
    ``approved_execute`` may expose shell tools, and the household adapter still
    provides no write primitive.
    """
    name = normalize_persona(persona)
    record = persona_record(name)
    approval_value = str(approval or "none").strip().lower()
    if approval_value not in VALID_APPROVALS:
        approval_value = "none"

    disabled = expand_tool_labels(record.get("blocked_tools") or [])
    mode = str(record.get("default_mode") or "read_only")
    read_only = approval_value != "approved_execute"
    if mode in {"plan", "read_only", "propose", "assist"} and approval_value != "approved_execute":
        read_only = True
    if mode == "execute_with_approval" and approval_value != "approved_execute":
        read_only = True
    if read_only:
        disabled.update(READ_ONLY_APPROVAL_BLOCKS)

    # Explicit execution approval is meaningful only for the operator persona.
    if approval_value == "approved_execute" and name != "lelouch":
        disabled.update({"bash", "python", "write_file", "edit_file"})
    return disabled


def policy_summary(persona: object, approval: object = "none") -> Dict[str, object]:
    name = normalize_persona(persona)
    record = persona_record(name)
    approval_value = str(approval or "none").strip().lower()
    if approval_value not in VALID_APPROVALS:
        approval_value = "none"
    blocked = sorted(persona_disabled_tools(name, approval_value))
    return {
        "persona": name,
        "role": record.get("role"),
        "approval": approval_value,
        "mode": "read_only" if approval_value != "approved_execute" else "approved_execute",
        "writes_allowed": False,
        "allowed_skill_categories": list(record.get("allowed_skill_categories") or []),
        "tools_blocked": blocked,
    }


def blocked_response(persona: object, tool: object, approval: object = "none") -> Dict[str, object]:
    name = normalize_persona(persona)
    tool_name = str(tool or "unknown")
    required = "lelouch with explicit execution approval" if tool_name in {"bash", "python", "shell"} else "a permitted persona and approval mode"
    return {
        "status": "blocked",
        "reason": f"Tool {tool_name} is blocked for persona {name}",
        "required_persona_or_approval": required,
        "policy": policy_summary(name, approval),
    }

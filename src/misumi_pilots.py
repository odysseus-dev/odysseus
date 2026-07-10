"""Controlled, disableable Phase A autonomy pilots."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from src.constants import BASE_DIR, DATA_DIR
from src.misumi_household import HouseholdReadOnlyAdapter
from src.misumi_skills import security_review_files, seed_catalog
from src.misumi_task_router import MisumiTaskRouter


CONFIG_PATH = Path(BASE_DIR) / "config" / "misumi_autonomy.json"


def load_pilot_config(path: Optional[Path | str] = None) -> Dict[str, object]:
    selected = path or os.getenv("MISUMI_AUTONOMY_CONFIG") or CONFIG_PATH
    return json.loads(Path(selected).read_text(encoding="utf-8-sig"))


def run_pilot(
    name: str,
    *,
    adapter: Optional[HouseholdReadOnlyAdapter] = None,
    question: str = "",
    persist: bool = False,
    output_root: Optional[Path | str] = None,
) -> Dict[str, object]:
    adapter = adapter or HouseholdReadOnlyAdapter()
    before = adapter.content_fingerprint() if adapter.reachable else None
    if name == "morning-status":
        routing = MisumiTaskRouter(adapter).route("What task should we do next?", persona="aoteru", approval="approved_read_only")
        result = {
            "system": adapter.status(),
            "task": {key: routing.get(key) for key in ("status", "summary", "selected_task", "blockers")},
        }
    elif name == "skill-audit":
        reviews = []
        for skill in seed_catalog():
            text = Path(str(skill["path"])).read_text(encoding="utf-8")
            reviews.append({"name": skill["name"], **security_review_files({"SKILL.md": text})})
        result = {"skills_checked": len(reviews), "flagged": [item for item in reviews if item["flagged"]], "deleted": []}
    elif name == "task-triage":
        result = MisumiTaskRouter(adapter).route(
            "Autonomously complete agentic routed tasks.", persona="aoteru", approval="approved_read_only"
        )
    elif name == "household-qa":
        result = {"question": question, "sources": adapter.search(question, limit=10), "grounded": True}
    else:
        raise ValueError(f"Unknown Misumi pilot: {name}")

    after = adapter.content_fingerprint() if adapter.reachable else None
    envelope = {
        "pilot": name,
        "phase": "A",
        "timestamp": time.time(),
        "writes_allowed": False,
        "external_sends_allowed": False,
        "household_unchanged": before == after,
        "result": result,
    }
    if persist:
        root = Path(output_root or Path(DATA_DIR) / "misumi" / "pilots")
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{name}-{int(envelope['timestamp'])}.json"
        target.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        envelope["output"] = str(target)
    return envelope

"""Controlled, disableable Phase A autonomy pilots."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from src.constants import BASE_DIR, DATA_DIR
from src.misumi_household import HouseholdReadOnlyAdapter
from src.misumi_memory import MisumiMemory
from src.misumi_skills import security_review_files, seed_catalog
from src.misumi_task_router import MisumiTaskRouter


CONFIG_PATH = Path(BASE_DIR) / "config" / "misumi_autonomy.json"


def _household_snapshot(adapter: HouseholdReadOnlyAdapter):
    if not adapter.reachable or not adapter.root:
        return None
    snapshot = []
    for path in sorted(item for item in adapter.root.rglob("*") if item.is_file()):
        stat = path.stat()
        snapshot.append((path.relative_to(adapter.root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return snapshot


def _memory_digest(memory: MisumiMemory, adapter: HouseholdReadOnlyAdapter) -> Dict[str, object]:
    before = _household_snapshot(adapter)
    capsules, capsule_corrupt = memory.capsules()
    loops, loop_corrupt = memory.loops()
    handoffs, handoff_corrupt = memory.handoffs()
    capsules.sort(key=lambda item: str(item.get("created", "")), reverse=True)
    unresolved = [item for item in loops if item.get("status") == "open"]
    unresolved.sort(key=lambda item: (not bool(item.get("stale")), str(item.get("created", ""))))
    pending = [item for item in handoffs if item.get("status") == "pending"]
    pending.sort(key=lambda item: str(item.get("created", "")))
    glance = memory.glance()

    def lines(items, formatter):
        return [f"- {formatter(item)}" for item in items] or ["- None"]

    content = "\n".join((
        f"# Misumi memory digest — {datetime.now(timezone.utc).date().isoformat()}",
        "", "## Recent captures",
        *lines(capsules[:20], lambda item: f"[{item.get('persona_primary')}] {item.get('summary')}"),
        "", "## Unresolved loops",
        *lines(unresolved, lambda item: f"[{item.get('owner')}] {item.get('text')}"),
        "", "## Stale items",
        *lines([item for item in unresolved if item.get("stale")], lambda item: f"[{item.get('owner')}] {item.get('text')}"),
        "", "## Pending handoffs",
        *lines(pending, lambda item: f"[{item.get('to_persona')}] {item.get('action')}"),
        "", "## Recommended next action",
        f"- Owner: {glance.get('responsible_persona') or 'None'}",
        f"- Action: {glance.get('next_recommended_action') or 'None'}",
        "",
    ))
    after = _household_snapshot(adapter)
    unchanged = before == after
    result: Dict[str, object] = {
        "household_unchanged": unchanged,
        "recent_captures": len(capsules[:20]),
        "unresolved_loops": len(unresolved),
        "stale_items": sum(1 for item in unresolved if item.get("stale")),
        "pending_handoffs": len(pending),
        "recommended_next_action": glance.get("next_recommended_action"),
        "owner": glance.get("responsible_persona"),
        "corrupt_lines": capsule_corrupt + loop_corrupt + handoff_corrupt,
    }
    if not unchanged:
        result["aborted"] = True
        return result
    target_dir = memory.root / "digests"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{datetime.now(timezone.utc).date().isoformat()}-digest.md"
    target.write_text(content, encoding="utf-8")
    result["output"] = str(target)
    return result


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
    memory_root: Optional[Path | str] = None,
) -> Dict[str, object]:
    adapter = adapter or HouseholdReadOnlyAdapter()
    before = (
        _household_snapshot(adapter)
        if name == "memory-digest"
        else adapter.content_fingerprint() if adapter.reachable else None
    )
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
    elif name == "memory-digest":
        result = _memory_digest(MisumiMemory(memory_root), adapter)
    else:
        raise ValueError(f"Unknown Misumi pilot: {name}")

    after = (
        _household_snapshot(adapter)
        if name == "memory-digest"
        else adapter.content_fingerprint() if adapter.reachable else None
    )
    household_unchanged = before == after
    if name == "memory-digest":
        household_unchanged = bool(result.get("household_unchanged")) and household_unchanged
        if not household_unchanged:
            output = result.pop("output", None)
            if output:
                Path(str(output)).unlink(missing_ok=True)
            result.update({"household_unchanged": False, "aborted": True})
    envelope = {
        "pilot": name,
        "phase": "A",
        "timestamp": time.time(),
        "writes_allowed": False,
        "external_sends_allowed": False,
        "household_unchanged": household_unchanged,
        "result": result,
    }
    if persist:
        root = Path(output_root or Path(DATA_DIR) / "misumi" / "pilots")
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{name}-{int(envelope['timestamp'])}.json"
        target.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        envelope["output"] = str(target)
    return envelope

"""Read-only discovery, ranking, and planning for household task files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from src.misumi_household import HouseholdReadOnlyAdapter
from src.misumi_policy import normalize_persona, policy_summary


QUEUES = ("agent-tasks/inbox", "agent-tasks/odysseus", "agent-tasks/misumi", "agent-tasks/review", "agent-tasks/blocked-human")
DONE_STATUSES = {"done", "complete", "completed", "closed", "archived", "rejected"}
RANKING = (
    (100, ("deploy-odysseus", "runtime deployment", "runtime health", "host-service-health")),
    (90, ("compatibility", "misumi/odysseus", "odysseus-integration")),
    (80, ("persona policy", "skill scoping", "tool policy")),
    (70, ("household-domains", "household data", "read-only")),
    (60, ("observability", "eval")),
    (50, ("household script", "shopping", "cleaning", "records", "plants")),
    (20, ("voice", "stt", "tts", "wakeword", "wake-word")),
    (10, ("avatar", "animation", "visual", "aesthetic", "portrait")),
)


def _frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    result: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.split("#", 1)[0].strip().strip("'\"")
    return result


class MisumiTaskRouter:
    def __init__(self, adapter: HouseholdReadOnlyAdapter):
        self.adapter = adapter

    def discover(self) -> List[Dict[str, object]]:
        if not self.adapter.root:
            return []
        candidates = []
        for queue in QUEUES:
            folder = self.adapter.root / queue
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.md")):
                text = path.read_text(encoding="utf-8", errors="replace")
                meta = _frontmatter(text)
                status = (meta.get("status") or "open").lower()
                if status in DONE_STATUSES:
                    continue
                title = meta.get("title") or re.sub(r"[-_]", " ", path.stem).strip().title()
                rel = path.relative_to(self.adapter.root).as_posix()
                item = {
                    "path": rel,
                    "title": title,
                    "status": status,
                    "priority": (meta.get("priority") or "normal").lower(),
                    "owner_target": meta.get("owner_target") or meta.get("owner") or None,
                    "queue": queue.rsplit("/", 1)[-1],
                }
                item["score"] = self._score(item, text[:3000])
                candidates.append(item)
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
        return candidates

    @staticmethod
    def _score(item: Dict[str, object], text: str) -> int:
        haystack = f"{item.get('path')} {item.get('title')} {text}".lower()
        score = 0
        for value, needles in RANKING:
            if any(needle in haystack for needle in needles):
                score = max(score, value)
        score += {"critical": 25, "high": 15, "medium": 5, "low": -5}.get(str(item.get("priority")), 0)
        score += {"odysseus": 15, "misumi": 15, "inbox": 5, "review": -10, "blocked-human": -40}.get(str(item.get("queue")), 0)
        if "blocked" in str(item.get("status")):
            score -= 30
        return score

    def _plan_for(self, selected: Dict[str, object]) -> List[str]:
        title = str(selected.get("title") or "task").lower()
        plan = ["read the task and referenced contracts", "inspect the current implementation and tests"]
        if "deploy" in title or "health" in title:
            plan.extend(["make the smallest reversible operations change", "run readiness and rollback smoke checks"])
        elif "observability" in title or "eval" in title:
            plan.extend(["add a focused fixture or event field", "run the smallest relevant eval subset"])
        else:
            plan.extend(["prepare a read-only implementation plan", "validate that the household repository is unchanged"])
        return plan

    def route(
        self,
        prompt: str,
        *,
        persona: object = "aoteru",
        approval: object = "none",
        selected_task: Optional[str] = None,
    ) -> Dict[str, object]:
        name = normalize_persona(persona)
        candidates = self.discover()
        if not self.adapter.reachable:
            return {
                "status": "blocked",
                "summary": "The canonical household repository is not reachable.",
                "selected_task": None,
                "task_candidates": [],
                "actions_taken": [],
                "files_read": [],
                "files_changed": [],
                "validation": [],
                "blockers": ["Configure MISUMI_HOUSEHOLD_ROOT."],
                "next_human_action": "Configure the read-only household repository path.",
                "source": "odysseus-task-router",
                "persona": name,
                "policy": policy_summary(name, approval),
            }
        if not candidates:
            return {
                "status": "blocked",
                "summary": "No open file tasks were found.",
                "selected_task": None,
                "task_candidates": [],
                "actions_taken": ["scanned documented task queues"],
                "files_read": [],
                "files_changed": [],
                "validation": ["read-only queue scan completed"],
                "blockers": ["No open task candidate exists."],
                "next_human_action": "Create or route a task file.",
                "source": "odysseus-task-router",
                "persona": name,
                "policy": policy_summary(name, approval),
            }

        selected = None
        if selected_task:
            normalized = selected_task.replace("\\", "/")
            selected = next((item for item in candidates if item["path"] == normalized), None)
        selected = selected or candidates[0]
        blockers = ["Human action is required before execution."] if selected.get("queue") == "blocked-human" else []
        plan = self._plan_for(selected)
        handoff = (
            f"Implement {selected['path']} in its owning repository. Preserve Phase A read-only household access, "
            f"follow the referenced contracts, run focused tests, and report files changed and rollback steps."
        )
        return {
            "status": "blocked" if blockers else "planned",
            "summary": f"Recommended {selected['title']} as the highest-ranked safe task.",
            "selected_task": selected["path"],
            "why_selected": "highest current critical-path score; no lower-priority voice or aesthetic work selected",
            "task_candidates": candidates[:10],
            "plan": plan,
            "actions_taken": ["scanned documented task queues", "ranked candidates", "generated a read-only plan"],
            "files_read": [selected["path"]],
            "files_changed": [],
            "validation": ["household adapter exposes no write operation"],
            "blockers": blockers,
            "blocked_by": blockers,
            "safe_to_execute_now": False,
            "recommended_executor": "Codex",
            "handoff_prompt": handoff,
            "next_human_action": "Resolve the listed blocker." if blockers else None,
            "source": "odysseus-task-router",
            "persona": name,
            "policy": policy_summary(name, approval),
        }

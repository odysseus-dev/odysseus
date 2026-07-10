"""Misumi compatibility, policy, household, task, and status API."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.misumi_household import HouseholdReadOnlyAdapter, infer_household_domain
from src.misumi_observability import MisumiEventLog
from src.misumi_policy import load_persona_policy, normalize_persona, persona_record, policy_summary
from src.misumi_skills import installed_skill_files, security_review_files, skills_for_persona
from src.misumi_task_router import MisumiTaskRouter


class MisumiRespondRequest(BaseModel):
    prompt: str = ""
    intent: str = "reply"
    state: str = "idle"
    mood: str = "focused"
    context: Dict[str, object] = Field(default_factory=dict)
    persona: str = "aoteru"


class MisumiTaskRequest(BaseModel):
    prompt: str
    persona: str = "aoteru"
    mode: str = "task"
    approval: str = "none"
    selected_task: Optional[str] = None


class MisumiSkillImportRequest(BaseModel):
    url: str
    persona: str = "aoteru"
    category: Optional[str] = None


def _owner(request: Request) -> Optional[str]:
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None)
    return getattr(request.state, "current_user", None)


def _require_api_scope(request: Request, required: str) -> None:
    if not getattr(request.state, "api_token", False):
        return
    scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    accepted = {"*", "admin", "misumi", required}
    if required == "misumi:read":
        accepted.add("chat")
    if not scopes.intersection(accepted):
        raise HTTPException(403, f"API token requires {required} scope")


def _short_text(value: object, limit: int = 420) -> str:
    text = re.sub(r"<think>.*?</think>", "", str(value or ""), flags=re.I | re.S)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip()


async def _model_reply(prompt: str, persona: str) -> tuple[str, Optional[str], Optional[str]]:
    """Return text, backend, model; degrade honestly when no endpoint works."""
    fallback_url = (os.getenv("MISUMI_MODEL_URL") or os.getenv("MISUMI_OLLAMA_URL") or "").strip()
    fallback_model = (os.getenv("MISUMI_MODEL") or "").strip()
    try:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        from src.seed_order_context import build_seed_order_context

        url, model, headers = resolve_endpoint(
            "default",
            fallback_url=fallback_url or None,
            fallback_model=fallback_model or None,
            owner=None,
        )
        if not url or not model:
            raise RuntimeError("no model endpoint configured")
        record = persona_record(persona)
        system = (
            f"You are {persona}, the Misumi {record.get('role')}. Answer concisely and never claim an action "
            "unless a structured tool result proves it. Phase A household access is read-only."
        )
        seed = build_seed_order_context()
        messages = []
        if seed:
            messages.append({"role": "system", "content": seed})
        messages.extend((
            {"role": "system", "content": system},
            {"role": "user", "content": prompt[:4000]},
        ))
        text = await llm_call_async(url, model, messages, max_tokens=160, timeout=25)
        return _short_text(text), str(url), str(model)
    except Exception:
        return "Odysseus is available, but no working model backend is configured for this request.", None, None


def setup_misumi_routes(skills_manager, task_scheduler=None, memory_vector=None) -> APIRouter:
    router = APIRouter(prefix="/misumi", tags=["misumi"])
    adapter = HouseholdReadOnlyAdapter()
    task_router = MisumiTaskRouter(adapter)
    events = MisumiEventLog()

    @router.get("/health")
    async def health():
        return {
            "status": "ok",
            "node": "odysseus-misumi",
            "source": "odysseus",
            "phase": "A",
            "auth_required_for_actions": True,
            "household_reachable": adapter.reachable,
        }

    @router.post("/respond")
    async def respond(request: Request, body: MisumiRespondRequest):
        _require_api_scope(request, "misumi:read")
        started = time.monotonic()
        request_id = events.request_id()
        persona = normalize_persona(body.persona)
        prompt = (body.prompt or body.intent or "status").strip()
        domain = infer_household_domain(prompt)
        sources = adapter.search(prompt, domain=domain, limit=4) if adapter.reachable else []
        backend = model = None
        if sources:
            lead = sources[0]
            text = _short_text(f"From {lead['path']} line {lead['line']}: {lead['snippet']}")
            backend = "household-read-only"
        elif domain:
            present = any(item["id"] == domain and item["present"] for item in adapter.domains())
            if present:
                text = f"No matching {domain} fact was found in the canonical household repository."
            else:
                text = f"The canonical household repository has no {domain} data surface yet."
            backend = "household-read-only"
        else:
            text, backend, model = await _model_reply(prompt, persona)
        outcome = "grounded" if sources else "absent" if domain else "model" if backend else "degraded"
        events.emit({
            "request_id": request_id,
            "persona": persona,
            "files_read": sorted({item["path"] for item in sources}),
            "files_changed": [],
            "model": model,
            "backend": backend,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "outcome": outcome,
            "approval_mode": "none",
        })
        return {
            "text": text,
            "state": "speaking",
            "mood": body.mood or "focused",
            "source": "odysseus",
            "persona": persona,
            "who": persona_record(persona).get("display_name"),
            "audio_url": None,
            "voice": None,
            "tts_provider": None,
            "request_id": request_id,
            "sources": sources,
        }

    @router.post("/task")
    async def task(request: Request, body: MisumiTaskRequest):
        _require_api_scope(request, "misumi:execute")
        started = time.monotonic()
        request_id = events.request_id()
        result = task_router.route(
            body.prompt,
            persona=body.persona,
            approval=body.approval,
            selected_task=body.selected_task,
        )
        result["request_id"] = request_id
        events.emit({
            "request_id": request_id,
            "persona": result.get("persona"),
            "task_id": result.get("selected_task"),
            "files_read": result.get("files_read"),
            "files_changed": [],
            "blocked_tools": (result.get("policy") or {}).get("tools_blocked"),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "outcome": result.get("status"),
            "blocker": "; ".join(result.get("blockers") or []),
            "approval_mode": body.approval,
        })
        return result

    @router.get("/personas")
    async def personas(request: Request):
        _require_api_scope(request, "misumi:read")
        return {
            "personas": [
                {"id": name, **record}
                for name, record in sorted(load_persona_policy().items())
            ],
            "head_persona": "aoteru",
            "security_principal": "odysseus",
        }

    @router.get("/personas/{persona}/skills")
    async def persona_skills(request: Request, persona: str):
        _require_api_scope(request, "misumi:read")
        name = normalize_persona(persona)
        installed = skills_manager.load(owner=_owner(request))
        visible = skills_for_persona(name, installed)
        return {
            "persona": name,
            "categories": persona_record(name).get("allowed_skill_categories"),
            "skills": visible,
            "count": len(visible),
        }

    @router.post("/skills/import-draft")
    async def import_draft(request: Request, body: MisumiSkillImportRequest):
        require_admin(request)
        from services.memory.skill_importer import SkillImportError, fetch_skill_bundle

        persona = normalize_persona(body.persona)
        categories = list(persona_record(persona).get("allowed_skill_categories") or [])
        category = body.category if body.category in categories else categories[0]
        try:
            files, _source = fetch_skill_bundle(body.url.strip())
            review = security_review_files(files)
            entry = skills_manager.import_bundle_from_files(
                files,
                owner=_owner(request),
                source_url=body.url.strip(),
                category=str(category),
            )
        except SkillImportError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "status": "draft",
            "persona": persona,
            "skill": entry,
            "security_review": review,
            "scripts_executed": False,
        }

    @router.get("/skills/security-review/{skill_name}")
    async def security_review(request: Request, skill_name: str):
        require_admin(request)
        installed = skills_manager.load(owner=_owner(request))
        skill = next((item for item in installed if item.get("name") == skill_name), None)
        if not skill:
            raise HTTPException(404, "Skill not found")
        return {"skill": skill_name, **security_review_files(installed_skill_files(skill))}

    @router.post("/personas/{persona}/skills/audit")
    async def audit_persona_skills(request: Request, persona: str):
        require_admin(request)
        name = normalize_persona(persona)
        visible = skills_for_persona(name, skills_manager.load(owner=_owner(request)))
        results = []
        for skill in visible:
            files = installed_skill_files(skill) if not skill.get("first_party") else {"SKILL.md": Path(str(skill["path"])).read_text(encoding="utf-8")}
            results.append({"name": skill.get("name"), **security_review_files(files)})
        return {"persona": name, "results": results, "count": len(results), "publication_changed": False}

    @router.get("/status")
    async def status(request: Request):
        _require_api_scope(request, "misumi:read")
        from src.readiness import check_readiness

        readiness = check_readiness(
            skills_manager=skills_manager,
            task_scheduler=task_scheduler,
            memory_vector=memory_vector,
        )
        candidates = task_router.discover()
        installed = skills_manager.load(owner=_owner(request))
        return {
            "status": "ready" if readiness.get("ready") else "degraded",
            "source": "odysseus-misumi-status",
            "phase": "A",
            "readiness": readiness,
            "household": adapter.status(),
            "tasks": {
                "count": len(candidates),
                "queues": {queue: sum(1 for item in candidates if item.get("queue") == queue) for queue in {item.get("queue") for item in candidates}},
            },
            "skills": {
                "installed": len(installed),
                "by_persona": {name: len(skills_for_persona(name, installed)) for name in load_persona_policy()},
            },
            "events": {"recent_count": len(events.recent(100))},
            "writes_allowed": False,
        }

    return router

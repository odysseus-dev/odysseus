"""Misumi compatibility, policy, household, task, and status API."""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.misumi_household import HouseholdReadOnlyAdapter, infer_household_domain
from src.misumi_memory import MisumiMemory
from src.misumi_observability import MisumiEventLog
from src.misumi_policy import load_persona_policy, normalize_persona, persona_record, policy_summary
from src.misumi_skills import installed_skill_files, security_review_files, skills_for_persona
from src.misumi_task_router import MisumiTaskRouter


logger = logging.getLogger(__name__)

_HONESTY_CONSTRAINTS = (
    "Answer concisely and never claim an action unless a structured tool result proves it. "
    "Phase A household access is read-only."
)
_RATIFICATION_CONSTRAINT = "Household changes go through proposals that the user ratifies."
_NEUTRAL_HANDOFF_ACTION = "review the plan and contribute next steps"


class MisumiRespondRequest(BaseModel):
    prompt: str = ""
    intent: str = "reply"
    state: str = "idle"
    mood: str = "focused"
    context: Union[Dict[str, object], str] = Field(default_factory=dict)
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


class MisumiMemoryCaptureRequest(BaseModel):
    text: str
    source: str = "chat"
    type: Optional[str] = None
    persona: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    next_action: Optional[str] = None
    meta: Dict[str, object] = Field(default_factory=dict)


class MisumiMemoryRouteRequest(BaseModel):
    persona_primary: str
    persona_secondary: Optional[str] = None


class MisumiMemoryCloseRequest(BaseModel):
    resolution: Optional[str] = None


class MisumiHandoffRequest(BaseModel):
    from_persona: str
    to_persona: str
    action: str
    capsule_id: Optional[str] = None
    note: Optional[str] = None


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


def _consultation_enabled() -> bool:
    value = (os.getenv("MISUMI_CONSULT", "1") or "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _term_positions(text: str, terms: List[str]) -> List[int]:
    positions = []
    for term in terms:
        match = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.I)
        if match:
            positions.append(match.start())
    return positions


def _intent_score(prompt: str, intents: List[str]) -> int:
    normalized_prompt = re.sub(r"[-_]", " ", prompt.lower())
    score = 0
    for intent in intents:
        normalized_intent = re.sub(r"[-_]", " ", intent.lower()).strip()
        terms = [normalized_intent]
        if " " in normalized_intent:
            terms.extend(part for part in normalized_intent.split() if len(part) >= 3)
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized_prompt) for term in terms):
            score += 1
    return score


def _consultation_plan(prompt: str, primary_reply: str, persona: str) -> List[str]:
    """Choose at most two synchronous consultation targets deterministically."""
    if persona != "aoteru" or not _consultation_enabled():
        return []

    from src.persona_capabilities import consult_edges, routing_intents

    policy_order = list(load_persona_policy())
    allowed = set(policy_order) - {"aoteru"}
    edges = [item.lower() for item in (consult_edges("aoteru") or [])]
    edges = [item for item in edges if item in allowed]
    intent_scores = {
        item: _intent_score(prompt, routing_intents(item) or [])
        for item in policy_order
        if item in allowed
    }
    candidates = list(dict.fromkeys(edges + [item for item in policy_order if intent_scores.get(item, 0)]))

    def rank(item: str) -> tuple[int, int, int, int]:
        record = persona_record(item)
        terms = list(dict.fromkeys((item, str(record.get("display_name") or item))))
        mentions = _term_positions(primary_reply, terms)
        if mentions:
            return (0, min(mentions), 0, policy_order.index(item))
        score = intent_scores.get(item, 0)
        if score:
            return (1, 0, -score, policy_order.index(item))
        return (2, edges.index(item), 0, policy_order.index(item))

    return sorted(candidates, key=rank)[:2]


async def _consult_persona(
    prompt: str,
    primary_reply: str,
    persona: str,
    backend: str,
    model: str,
) -> str:
    from src.llm_core import llm_call_async
    from src.persona_capabilities import capability_summary
    from src.seed_order_context import build_seed_order_context

    record = persona_record(persona)
    system = (
        f"You are {persona}, the Misumi {record.get('role')}. Review Aoteru's plan and contribute "
        f"concise, practical critique or next steps. {_HONESTY_CONSTRAINTS}"
    )
    capabilities = capability_summary(persona)
    if capabilities:
        system += f"\n\n{capabilities}"
    system += f"\n{_RATIFICATION_CONSTRAINT}"
    seed = build_seed_order_context()
    messages = []
    if seed:
        messages.append({"role": "system", "content": seed})
    messages.extend((
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"User prompt:\n{prompt[:4000]}\n\n"
                f"Aoteru's plan under discussion:\n{_short_text(primary_reply, 600)}"
            ),
        },
    ))
    text = await llm_call_async(
        backend,
        model,
        messages,
        max_tokens=240,
        timeout=20,
        allow_reasoning_fallback=False,
    )
    contribution = _short_text(text, 1200)
    if not contribution:
        raise RuntimeError("consulted model returned empty content")
    return contribution


def _handoff_action(contribution: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", contribution, maxsplit=1)[0]
    return _short_text(sentence, 200)


async def _model_reply(prompt: str, persona: str) -> tuple[str, Optional[str], Optional[str]]:
    """Return text, backend, model; degrade honestly when no endpoint works."""
    fallback_url = (os.getenv("MISUMI_MODEL_URL") or os.getenv("MISUMI_OLLAMA_URL") or "").strip()
    fallback_model = (os.getenv("MISUMI_MODEL") or "").strip()
    try:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        from src.persona_capabilities import capability_summary
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
        system = f"You are {persona}, the Misumi {record.get('role')}. {_HONESTY_CONSTRAINTS}"
        capabilities = capability_summary(persona)
        if capabilities:
            system += f"\n\n{capabilities}"
        system += f"\n{_RATIFICATION_CONSTRAINT}"
        seed = build_seed_order_context()
        messages = []
        if seed:
            messages.append({"role": "system", "content": seed})
        messages.extend((
            {"role": "system", "content": system},
            {"role": "user", "content": prompt[:4000]},
        ))
        text = await llm_call_async(
            url,
            model,
            messages,
            max_tokens=480,
            timeout=25,
            allow_reasoning_fallback=False,
        )
        reply = _short_text(text)
        if not reply:
            raise RuntimeError("model returned empty content (reasoning-only)")
        return reply, str(url), str(model)
    except Exception as exc:
        logger.exception("Misumi model reply failed: %s", exc)
        return "Odysseus is available, but no working model backend is configured for this request.", None, None


def setup_misumi_routes(skills_manager, task_scheduler=None, memory_vector=None, memory_root=None) -> APIRouter:
    router = APIRouter(prefix="/misumi", tags=["misumi"])
    adapter = HouseholdReadOnlyAdapter()
    task_router = MisumiTaskRouter(adapter)
    events = MisumiEventLog()
    memory = MisumiMemory(memory_root)

    def memory_call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "Memory record not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "Misumi memory store is unavailable") from exc

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
        interface_context = body.context if isinstance(body.context, str) else ""
        prompt = (body.prompt or interface_context or body.intent or "status").strip()
        domain = infer_household_domain(prompt)
        sources = adapter.search(prompt, domain=domain, limit=4) if adapter.reachable else []
        if not domain:
            sources = [item for item in sources if int(item.get("score", 0)) >= 2]
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

        consulted = []
        capsule_id = None
        handoff_ids = []
        if outcome == "model" and backend and model:
            primary_reply = text
            targets = _consultation_plan(prompt, primary_reply, persona)
            contributions = []
            for target in targets:
                try:
                    contribution = await _consult_persona(
                        prompt, primary_reply, target, backend, model
                    )
                except Exception as exc:
                    logger.warning(
                        "Misumi consultation failed for %s: %s", target, exc,
                        exc_info=True,
                    )
                    continue
                contributions.append((target, contribution))
                consulted.append({"persona": target})
                text += f"\n\n— {persona_record(target).get('display_name')}: {contribution}"

            if targets:
                capsule_type = (
                    "decision"
                    if re.search(r"\b(plan|planning|decide|deciding|decision)\b", prompt, re.I)
                    else "observation"
                )
                try:
                    capsule = memory.capture(
                        f"{prompt}\n\nAoteru's plan: {_short_text(primary_reply, 600)}",
                        source="consultation",
                        capsule_type=capsule_type,
                        persona="aoteru",
                    )
                    capsule_id = str(capsule["id"])
                except Exception as exc:
                    logger.warning("Misumi consultation capsule failed: %s", exc, exc_info=True)

                for target, contribution in (contributions if capsule_id else []):
                    action = _handoff_action(contribution)
                    try:
                        try:
                            handoff = memory.create_handoff(
                                "aoteru", target, action, capsule_id
                            )
                        except ValueError as exc:
                            logger.warning(
                                "Misumi consultation handoff action rejected for %s: %s; using neutral action",
                                target, exc,
                            )
                            handoff = memory.create_handoff(
                                "aoteru", target, _NEUTRAL_HANDOFF_ACTION, capsule_id
                            )
                        handoff_ids.append(str(handoff["id"]))
                    except Exception as exc:
                        logger.warning(
                            "Misumi consultation handoff failed for %s: %s", target, exc,
                            exc_info=True,
                        )
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
        response = {
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
        if _consultation_enabled():
            response.update({
                "consulted": consulted,
                "capsule_id": capsule_id,
                "handoff_ids": handoff_ids,
            })
        return response

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
        memory_state = memory_call(memory.glance)
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
            "memory": {
                "capsules": len(memory_call(memory.capsules)[0]),
                "inbox": memory_state["inbox_count"],
                "open_loops": memory_state["open_loop_count"],
                "stale_loops": memory_state["stale_loop_count"],
                "pending_handoffs": memory_state["pending_handoff_count"],
                "newest_capture": memory_state["newest_capture"],
                "top_open_loop": memory_state["top_open_loop"],
                "next_recommended_action": memory_state["next_recommended_action"],
                "responsible_persona": memory_state["responsible_persona"],
                "writes_allowed": False,
            },
            "writes_allowed": False,
        }

    @router.post("/memory/capture")
    async def capture_memory(request: Request, body: MisumiMemoryCaptureRequest):
        _require_api_scope(request, "misumi:execute")
        return memory_call(
            memory.capture, body.text, source=body.source, capsule_type=body.type,
            persona=body.persona, entities=body.entities, next_action=body.next_action,
            meta=body.meta,
        )

    @router.get("/memory/inbox")
    async def memory_inbox(request: Request, limit: int = 20):
        _require_api_scope(request, "misumi:read")
        capsules, corrupt = memory_call(memory.capsules)
        selected = [item for item in capsules if item.get("status") == "open" and not item.get("human_confirmed")]
        selected.sort(key=lambda item: str(item.get("created", "")), reverse=True)
        return {"capsules": selected[:max(1, min(limit, 100))], "corrupt_lines": corrupt}

    @router.get("/memory/recent")
    async def memory_recent(request: Request, limit: int = 20):
        _require_api_scope(request, "misumi:read")
        capsules, corrupt = memory_call(memory.capsules)
        capsules.sort(key=lambda item: str(item.get("created", "")), reverse=True)
        return {"capsules": capsules[:max(1, min(limit, 100))], "corrupt_lines": corrupt}

    @router.get("/memory/open-loops")
    async def memory_open_loops(request: Request):
        _require_api_scope(request, "misumi:read")
        loops, corrupt = memory_call(memory.loops)
        selected = [item for item in loops if item.get("status") == "open"]
        selected.sort(key=lambda item: str(item.get("created", "")))
        return {"open_loops": selected, "corrupt_lines": corrupt}

    @router.post("/memory/{capsule_id}/confirm")
    async def confirm_memory(request: Request, capsule_id: str):
        _require_api_scope(request, "misumi:execute")
        return memory_call(memory.confirm, capsule_id)

    @router.post("/memory/{capsule_id}/route")
    async def route_memory(request: Request, capsule_id: str, body: MisumiMemoryRouteRequest):
        _require_api_scope(request, "misumi:execute")
        return memory_call(memory.reroute, capsule_id, body.persona_primary, body.persona_secondary)

    @router.post("/memory/{capsule_id}/close")
    async def close_memory(request: Request, capsule_id: str, body: Optional[MisumiMemoryCloseRequest] = None):
        _require_api_scope(request, "misumi:execute")
        return memory_call(memory.close, capsule_id, body.resolution if body else None)

    @router.post("/handoff")
    async def create_handoff(request: Request, body: MisumiHandoffRequest):
        _require_api_scope(request, "misumi:execute")
        return memory_call(
            memory.create_handoff, body.from_persona, body.to_persona, body.action,
            body.capsule_id, body.note,
        )

    @router.get("/handoffs")
    async def list_handoffs(request: Request, status: Optional[str] = None):
        _require_api_scope(request, "misumi:read")
        if status is not None and status not in {"pending", "resolved"}:
            raise HTTPException(422, "Unknown handoff status")
        handoffs, corrupt = memory_call(memory.handoffs)
        selected = [item for item in handoffs if status is None or item.get("status") == status]
        selected.sort(key=lambda item: str(item.get("created", "")), reverse=True)
        return {"handoffs": selected, "corrupt_lines": corrupt}

    @router.post("/handoffs/{handoff_id}/resolve")
    async def resolve_handoff(request: Request, handoff_id: str):
        _require_api_scope(request, "misumi:execute")
        return memory_call(memory.resolve_handoff, handoff_id)

    @router.get("/glance")
    async def glance(request: Request):
        _require_api_scope(request, "misumi:read")
        return memory_call(memory.glance)

    return router

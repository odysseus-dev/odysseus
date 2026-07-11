"""
factory_orchestrator.py — Automated project planning + execution engine.

Pipeline:
  1. Fredrix (Planner)   — decomposes description into a DAG of typed tasks.
  2. Stoffe (Architect)  — reviews the plan, produces architecture directives.
  3. Orchestrator loop   — routes each task to a specialist producer + reviewer:

       task_type   Producer          Reviewer
       ─────────   ────────          ────────
       backend     Chris (code)      Tess
       frontend    Fia  (UI/docs)    Sara
       network     Nova (net/sec)    Vera
       devops      Atlas (infra)     Vera
       (default)   Chris             Tess

  4. Produce → Review → Approve/Reject loop (max MAX_ATTEMPTS, then human_intervention).

Uses the same endpoint resolution as background tasks (task_llm_call_async)
so it respects the user's configured model/endpoint settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.factory_service import FactoryService

logger = logging.getLogger(__name__)
_service = FactoryService()

MAX_ATTEMPTS = 10

_running: Dict[int, asyncio.Task] = {}
_planning_tasks: set = set()  # strong refs so GC doesn't kill them


# ═══════════════════════════════════════════════════════════════
# AGENT ROSTER — 9 specialist agents
# ═══════════════════════════════════════════════════════════════

AGENTS: Dict[str, Dict[str, str]] = {

    # ── Planning & Architecture ──────────────────────────────

    "fredrix": {
        "name": "Fredrix",
        "role": "Planner",
        "system": (
            "You are Fredrix, a senior project planner. Break the user's project "
            "description into concrete, executable tasks. Assign each task a "
            "task_type so it can be routed to the right specialist:\n"
            "  backend  — server logic, databases, algorithms\n"
            "  frontend — UI, design, user-facing code, documentation\n"
            "  network  — APIs, networking, security, auth, web protocols\n"
            "  devops   — deployment, CI/CD, containers, infrastructure, config\n\n"
            "Return ONLY a JSON object — no markdown, no commentary:\n\n"
            '{\n'
            '  "architecture": "2-5 sentences of cross-cutting directives (tech stack, conventions, shared interfaces)",\n'
            '  "tasks": [\n'
            '    {"title": "...", "description": "...", "task_type": "backend|frontend|network|devops", "dependencies": [0]},\n'
            "    ...\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- 3-10 tasks, ordered so dependencies come first.\n"
            '- "dependencies" uses 0-based indices into the tasks array.\n'
            "- Keep titles under 60 chars. Descriptions 1-3 sentences.\n"
            "- Be specific and actionable."
        ),
    },

    "stoffe": {
        "name": "Stoffe",
        "role": "Architect",
        "system": (
            "You are Stoffe, the system architect. Given a project description and "
            "a list of planned tasks, produce concise architecture directives that "
            "each producer should follow. Cover: tech stack recommendations, naming "
            "conventions, shared interfaces between tasks, and any cross-cutting "
            "concerns (error handling, security, performance).\n\n"
            "Be concise — 5-15 bullet points. The producers receive these verbatim."
        ),
    },

    # ── Producers ─────────────────────────────────────────────

    "chris": {
        "name": "Chris",
        "role": "Backend Producer",
        "system": (
            "You are Chris, a senior backend developer. You write clean, production-"
            "quality code for server logic, databases, algorithms, and data processing. "
            "Follow the architecture directives exactly. Output complete, working code "
            "with brief explanations where needed."
        ),
    },

    "fia": {
        "name": "Fia",
        "role": "Frontend Producer",
        "system": (
            "You are Fia, a senior frontend developer and designer. You create "
            "user interfaces, client-side code, styles, and documentation. You care "
            "about UX, accessibility, and clean design. Follow the architecture "
            "directives exactly. Output complete, working code or content."
        ),
    },

    "nova": {
        "name": "Nova",
        "role": "Network Specialist",
        "system": (
            "You are Nova, a network and security specialist. You handle API design, "
            "network protocols, authentication, encryption, web security, and "
            "infrastructure networking. You think about latency, reliability, and "
            "threat models. Follow the architecture directives exactly. Output "
            "complete configs, code, or analysis with clear reasoning."
        ),
    },

    "atlas": {
        "name": "Atlas",
        "role": "DevOps Engineer",
        "system": (
            "You are Atlas, a senior DevOps engineer. You handle deployment, CI/CD "
            "pipelines, containers (Docker/K8s), infrastructure-as-code, monitoring, "
            "and environment configuration. Follow the architecture directives "
            "exactly. Output complete configs, scripts, or infrastructure definitions."
        ),
    },

    # ── Reviewers ─────────────────────────────────────────────

    "tess": {
        "name": "Tess",
        "role": "Backend Reviewer",
        "system": (
            "You are Tess, a strict backend code reviewer. Evaluate the output for "
            "correctness, edge cases, performance, and security. Reject only for "
            "concrete problems — vague concerns are not grounds for rejection.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },

    "sara": {
        "name": "Sara",
        "role": "Frontend Reviewer",
        "system": (
            "You are Sara, a strict frontend and UX reviewer. Evaluate the output for "
            "usability, accessibility, visual consistency, and code quality. Reject "
            "only for concrete problems.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },

    "vera": {
        "name": "Vera",
        "role": "Network & Infra Reviewer",
        "system": (
            "You are Vera, a strict network, security, and infrastructure reviewer. "
            "Evaluate the output for security vulnerabilities, network reliability, "
            "correct configuration, and best practices. Reject only for concrete "
            "problems.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },
}

# ── Task routing: task_type → (producer_key, reviewer_key) ────

TASK_ROUTING: Dict[str, tuple] = {
    "backend":  ("chris", "tess"),
    "code":     ("chris", "tess"),
    "test":     ("chris", "tess"),
    "frontend": ("fia",   "sara"),
    "design":   ("fia",   "sara"),
    "ui":       ("fia",   "sara"),
    "docs":     ("fia",   "sara"),
    "network":  ("nova",  "vera"),
    "security": ("nova",  "vera"),
    "api":      ("nova",  "vera"),
    "devops":   ("atlas", "vera"),
    "infra":    ("atlas", "vera"),
}
DEFAULT_ROUTE: tuple = ("chris", "tess")


def _route(task_type: str) -> tuple:
    """Return (producer_key, reviewer_key) for a task_type."""
    return TASK_ROUTING.get((task_type or "").lower().strip(), DEFAULT_ROUTE)


# ═══════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════

def _extract_json(text: str) -> Optional[Any]:
    """Extract a JSON object from an LLM response that may have markdown fences."""
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except Exception:
            pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except Exception:
            pass
    return None


async def _llm(messages: List[Dict], owner: str, timeout: int = 120,
               max_tokens: int = 4096) -> str:
    """Call the configured task endpoint with fallback chain."""
    from src.task_endpoint import task_llm_call_async
    return await task_llm_call_async(
        messages, owner=owner, timeout=timeout, max_tokens=max_tokens,
    )


async def _call_agent(agent_key: str, user_prompt: str, owner: str,
                      timeout: int = 120, max_tokens: int = 4096) -> str:
    """Call a named agent with its system prompt."""
    agent = AGENTS[agent_key]
    return await _llm(
        [
            {"role": "system", "content": agent["system"]},
            {"role": "user", "content": user_prompt},
        ],
        owner=owner,
        timeout=timeout,
    )


# ═══════════════════════════════════════════════════════════════
# Phase 1: Planning (Fredrix)
# ═══════════════════════════════════════════════════════════════

async def plan_project(project_id: int, owner: str = "default") -> bool:
    """Decompose the project description into tasks via LLM, then auto-start."""
    project = _service.get_project(project_id)
    if not project:
        return False

    desc = project.get("description") or ""
    if not desc.strip():
        return False

    logger.info(f"Factory: Fredrix planning project {project_id} ({desc[:60]}...)")

    try:
        raw = await _call_agent("fredrix", f"Project description:\n{desc}", owner, timeout=120)
    except Exception as e:
        logger.error(f"Factory: planner failed for project {project_id}: {e}")
        _service.set_project_status(project_id, "failed")
        return False

    plan = _extract_json(raw)
    if not plan or not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        logger.error(f"Factory: invalid plan for project {project_id}:\n{raw[:300]}")
        _service.set_project_status(project_id, "failed")
        return False

    # Extract architecture directives from the planner output (folded in to
    # avoid a second blocking LLM call before the project can start).
    arch_directives = plan.get("architecture", "")
    if arch_directives:
        logger.info(f"Factory: architecture directives extracted ({len(arch_directives)} chars)")
        _service._log_event_safe(project_id, None, "Architecture directives generated",
                                 event_type="architecture_done")

    # Create nodes + dependency edges
    node_ids: List[int] = []
    for i, t in enumerate(plan["tasks"]):
        if not isinstance(t, dict):
            continue
        deps_indices = t.get("dependencies", []) or []
        deps = [node_ids[d] for d in deps_indices if isinstance(d, int) and 0 <= d < len(node_ids)]
        try:
            node = _service.add_node(
                project_id=project_id,
                task_type=t.get("task_type", "backend"),
                title=t.get("title", f"Task {i + 1}"),
                description=t.get("description", ""),
                dependencies=deps,
                assigned_agent=_route(t.get("task_type", ""))[0].capitalize(),
            )
            node_ids.append(node["id"])
        except Exception as e:
            logger.error(f"Factory: add_node {i} failed: {e}")
            node_ids.append(0)

    logger.info(f"Factory: planned {len(node_ids)} tasks for project {project_id}")

    # Start the project immediately — no separate architect call blocking
    try:
        _service.start_project(project_id)
    except Exception as e:
        logger.error(f"Factory: start failed for project {project_id}: {e}")
        return False

    launch(project_id, owner, arch_directives)
    return True


# ═══════════════════════════════════════════════════════════════
# Phase 2: Execution — Produce → Review pipeline
# ═══════════════════════════════════════════════════════════════

async def _produce(agent_key: str, task: Dict, feedback: str,
                   owner: str, project_desc: str, arch: str) -> str:
    """Routed producer agent produces output for a task."""
    agent = AGENTS[agent_key]
    prompt = f"Project: {project_desc}\n\nTask: {task.get('title', '')}\nDescription: {task.get('description', '')}\n"
    if arch:
        prompt += f"\nArchitecture directives:\n{arch}\n"
    prompt += "\nComplete this task now. Provide the full output."
    if feedback:
        prompt += f"\n\nPrevious attempt was rejected. Feedback: {feedback}\nAddress this and try again."

    return await _llm(
        [
            {"role": "system", "content": agent["system"]},
            {"role": "user", "content": prompt},
        ],
        owner=owner,
    )


async def _review(agent_key: str, task: Dict, output: str, owner: str) -> Dict:
    """Routed reviewer agent evaluates the output."""
    agent = AGENTS[agent_key]
    prompt = (
        f"Task: {task.get('title', '')}\n"
        f"Description: {task.get('description', '')}\n\n"
        f"Output to review:\n{output[:4000]}"
    )
    raw = await _llm(
        [
            {"role": "system", "content": agent["system"]},
            {"role": "user", "content": prompt},
        ],
        owner=owner,
    )
    result = _extract_json(raw)
    if result and isinstance(result, dict):
        return {
            "approved": bool(result.get("approved", False)),
            "feedback": result.get("feedback", ""),
        }
    logger.warning(f"Factory: unparseable review from {agent_key}: {raw[:200]}")
    return {"approved": True, "feedback": ""}


async def _process_task(project_id: int, task: Dict, owner: str,
                        project_desc: str, arch: str) -> None:
    """Run the Produce → Review pipeline for a single task."""
    task_id = task["id"]
    producer_key, reviewer_key = _route(task.get("task_type"))
    _service.update_task_status(task_id, "running")

    logger.info(
        f"Factory: task {task_id} [{task.get('task_type', '?')}] "
        f"→ {AGENTS[producer_key]['name']} (produce) → {AGENTS[reviewer_key]['name']} (review)"
    )

    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info(f"Factory: task {task_id} attempt {attempt}/{MAX_ATTEMPTS}")

        try:
            output = await _produce(producer_key, task, feedback, owner, project_desc, arch)
        except Exception as e:
            logger.error(f"Factory: produce failed for task {task_id}: {e}")
            _service.fail_task(task_id, error=f"Produce error: {e}")
            return

        try:
            review = await _review(reviewer_key, task, output, owner)
        except Exception as e:
            logger.error(f"Factory: review failed for task {task_id}: {e}")
            review = {"approved": True, "feedback": ""}

        if review["approved"]:
            logger.info(f"Factory: task {task_id} approved on attempt {attempt}")
            _service.complete_task(task_id, result={
                "output": output, "attempts": attempt,
                "producer": AGENTS[producer_key]["name"],
                "reviewer": AGENTS[reviewer_key]["name"],
            })
            return

        feedback = review.get("feedback", "")
        logger.info(f"Factory: task {task_id} rejected by {AGENTS[reviewer_key]['name']}: {feedback[:100]}")

    _service.update_task_status(task_id, "human_intervention")
    _service._log_event_safe(project_id, task_id,
                             f"Task requires human intervention after {MAX_ATTEMPTS} attempts "
                             f"(producer: {AGENTS[producer_key]['name']}, "
                             f"reviewer: {AGENTS[reviewer_key]['name']})")


# ═══════════════════════════════════════════════════════════════
# Orchestrator loop
# ═══════════════════════════════════════════════════════════════

async def _orchestrator_loop(project_id: int, owner: str,
                             arch: str = "") -> None:
    """Main loop: find ready tasks, process them, repeat until done or stuck."""
    logger.info(f"Factory: orchestrator started for project {project_id}")

    while True:
        try:
            project = _service.get_project(project_id)
        except Exception:
            break
        if not project:
            break
        status = project.get("status")
        if status in ("completed", "cancelled", "failed", "paused"):
            logger.info(f"Factory: orchestrator stopping — project {project_id} is {status}")
            break

        ready = _service.get_next_ready_tasks(project_id)
        if not ready:
            dag = _service.get_dag(project_id)
            if dag.get("pending_tasks", 0) == 0 and dag.get("running_tasks", 0) == 0:
                logger.info(f"Factory: project {project_id} — all tasks done")
                break
            await asyncio.sleep(3)
            continue

        project_desc = project.get("description", "")
        for task in ready:
            p = _service.get_project(project_id)
            if not p or p.get("status") in ("paused", "cancelled"):
                break
            try:
                await _process_task(project_id, task, owner, project_desc, arch)
            except Exception as e:
                logger.error(f"Factory: error processing task {task.get('id')}: {e}")
                try:
                    _service.fail_task(task["id"], error=str(e))
                except Exception:
                    pass

        await asyncio.sleep(1)

    _running.pop(project_id, None)
    logger.info(f"Factory: orchestrator finished for project {project_id}")


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def launch_planning(project_id: int, owner: str = "default") -> None:
    """Launch the planning phase as a background task with a strong reference."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    task = loop.create_task(plan_project(project_id, owner))
    _planning_tasks.add(task)
    task.add_done_callback(_planning_tasks.discard)
    logger.info(f"Factory: launched planning for project {project_id}")


def launch(project_id: int, owner: str = "default", arch: str = "") -> None:
    """Launch (or re-launch) the orchestrator for a project."""
    existing = _running.get(project_id)
    if existing and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    task = loop.create_task(_orchestrator_loop(project_id, owner, arch))
    _running[project_id] = task


def is_running(project_id: int) -> bool:
    t = _running.get(project_id)
    return t is not None and not t.done()


def stop(project_id: int) -> None:
    t = _running.pop(project_id, None)
    if t and not t.done():
        t.cancel()


def list_agents() -> List[Dict[str, str]]:
    """Return the agent roster for display."""
    return [
        {"key": k, "name": v["name"], "role": v["role"]}
        for k, v in AGENTS.items()
    ]

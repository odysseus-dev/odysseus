"""
swarm_manager.py

Central orchestrator for the Odysseus Swarm Intelligence Framework.

Responsibilities:
  1. Task decomposition — master agent analyses the query and selects workers
  2. Dependency graph  — builds a DAG, identifies parallelisable groups
  3. Worker dispatch   — calls ``run_worker()`` per role with shared context
  4. Result merge      — master synthesises all worker outputs
  5. Error recovery    — retries failed workers, logs degraded results
  6. Metrics           — tracks tokens, latency, and worker utilisation
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from src.swarm.swarm_types import (
    ExecutionStatus,
    SwarmDefinition,
    SwarmExecution,
    SwarmTask,
    TaskStatus,
)
from src.swarm.swarm_memory import SwarmMemory
from src.swarm.swarm_worker import run_worker

logger = logging.getLogger(__name__)

# Limits
MAX_WORKERS_PER_RUN = 8
MAX_RETRIES = 1
PLANNING_MAX_TOKENS = 4096
MERGE_MAX_TOKENS = 8192


def _sse(data: dict) -> str:
    """Format a dict as an SSE ``data:`` line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Planning prompt templates
# ---------------------------------------------------------------------------

_PLANNING_SYSTEM = """\
You are the **{master_name}** — the lead of the **{swarm_name}**.

Your team consists of the following specialists:
{worker_catalogue}

## Your Job
Given the user's request, you MUST:
1. Analyse which team members are needed for THIS specific request.
2. Do NOT activate everyone — only the specialists whose expertise is genuinely required.
3. Break the task into specific, actionable sub-tasks.
4. Assign each sub-task to exactly one specialist.
5. Identify which tasks can run in parallel (no dependencies) vs. sequentially.

## Response Format
You MUST respond with ONLY a JSON object (no markdown fences, no commentary):
{{
  "reasoning": "Brief explanation of your delegation strategy",
  "tasks": [
    {{"worker": "<worker_slug>", "task": "<specific sub-task description>", "depends_on": []}},
    {{"worker": "<worker_slug>", "task": "<specific sub-task description>", "depends_on": ["<slug of dependency>"]}}
  ],
  "skipped": [
    {{"worker": "<worker_slug>", "reason": "<why this specialist is not needed>"}}
  ]
}}

Rules:
- ``worker`` must be one of the slugs listed above.
- ``depends_on`` contains slugs (not task IDs) of workers whose output this task needs.
- Keep sub-tasks focused and non-overlapping.
- Prefer parallel execution where possible.
- Maximum {max_workers} workers per run.
"""

_MERGE_SYSTEM = """\
You are the **{master_name}** — the lead of the **{swarm_name}**.

Your team has completed their assigned tasks. Below are their outputs.

## Your Job
1. Review each specialist's contribution.
2. Synthesise them into a single, coherent, high-quality response.
3. Resolve any conflicts or contradictions between specialists.
4. Add your own expert perspective where needed.
5. Present the final answer to the user in a clear, well-structured format.

Do NOT just concatenate the outputs. Produce an integrated, polished response that reads as if one expert wrote it.
"""


class SwarmManager:
    """Central orchestrator — plan, dispatch, merge, report."""

    async def execute(
        self,
        swarm: SwarmDefinition,
        user_query: str,
        *,
        session_id: str = "",
        endpoint_url: str = "",
        model: str = "",
        messages: Optional[List[Dict]] = None,
        owner: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        context_length: int = 0,
        disabled_tools: Optional[Set[str]] = None,
    ) -> AsyncGenerator[str, None]:
        """Main entry point — streams SSE events for the full swarm execution.

        Yields SSE events covering planning, worker execution, merging,
        and final metrics.  The caller (chat route or swarm tool) pipes
        these directly to the client.
        """
        execution = SwarmExecution(
            swarm_id=swarm.id,
            swarm_name=swarm.name,
            session_id=session_id,
            user_query=user_query,
            owner=owner,
        )
        shared_memory = SwarmMemory(execution.id)

        # -- Phase 1: Planning -----------------------------------------------
        yield _sse({
            "type": "swarm_start",
            "swarm": swarm.name,
            "swarm_id": swarm.id,
            "master": swarm.master.name,
            "execution_id": execution.id,
            "domain": swarm.domain,
        })

        execution.status = ExecutionStatus.PLANNING
        try:
            plan = await self._plan(
                swarm=swarm,
                user_query=user_query,
                endpoint_url=endpoint_url,
                model=model,
                headers=headers,
                temperature=temperature,
                context_length=context_length,
            )
        except Exception as exc:
            logger.exception("Swarm planning failed")
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = time.time()
            yield _sse({
                "type": "swarm_error",
                "error": f"Planning failed: {exc}",
                "execution_id": execution.id,
            })
            yield "data: [DONE]\n\n"
            return

        execution.tasks = plan["tasks"]
        execution.skipped = plan["skipped"]
        execution.master_plan = plan["reasoning"]

        yield _sse({
            "type": "swarm_plan",
            "reasoning": plan["reasoning"],
            "tasks": [t.to_dict() for t in execution.tasks],
            "skipped": execution.skipped,
            "execution_id": execution.id,
        })

        # -- Phase 2: Execution -----------------------------------------------
        execution.status = ExecutionStatus.EXECUTING

        # Process tasks in dependency order — parallelise where possible
        while True:
            ready = execution.tasks_ready()
            if not ready:
                break

            # Respect max_parallel concurrency cap
            batch = ready[:swarm.max_parallel]

            # Run batch concurrently
            results = await asyncio.gather(
                *[
                    self._run_single_worker(
                        swarm=swarm,
                        task=task,
                        execution=execution,
                        shared_memory=shared_memory,
                        endpoint_url=endpoint_url,
                        model=model,
                        session_id=session_id,
                        owner=owner,
                        headers=headers,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        context_length=context_length,
                        disabled_tools=disabled_tools,
                        user_query=user_query,
                    )
                    for task in batch
                ],
                return_exceptions=True,
            )

            # Yield all accumulated SSE events from workers
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Worker batch error: %s", result)
                    continue
                if isinstance(result, list):
                    for event in result:
                        yield event

        # -- Phase 3: Merge ---------------------------------------------------
        execution.status = ExecutionStatus.MERGING

        yield _sse({
            "type": "swarm_merge",
            "master": swarm.master.name,
            "execution_id": execution.id,
        })

        try:
            final_response = ""
            async for event in self._merge(
                swarm=swarm,
                execution=execution,
                shared_memory=shared_memory,
                user_query=user_query,
                endpoint_url=endpoint_url,
                model=model,
                headers=headers,
                temperature=temperature,
                max_tokens=MERGE_MAX_TOKENS,
                context_length=context_length,
            ):
                yield event
                # Extract text from delta events
                if event.startswith("data: "):
                    try:
                        ev = json.loads(event[6:].strip())
                        if "delta" in ev:
                            final_response += ev["delta"]
                    except (json.JSONDecodeError, KeyError):
                        pass

            execution.final_response = final_response.strip()

        except Exception as exc:
            logger.exception("Swarm merge failed")
            # Fall back to concatenated worker outputs
            execution.final_response = self._fallback_merge(execution)
            yield _sse({"delta": execution.final_response})

        # -- Phase 4: Complete ------------------------------------------------
        execution.status = ExecutionStatus.DONE
        execution.completed_at = time.time()

        # Sum tokens from all workers
        execution.total_tokens = sum(
            t.metrics.get("tokens", 0) for t in execution.tasks
        )

        # Persist shared memory if configured
        if swarm.memory_config.get("persist_after", False):
            await shared_memory.persist(session_id=session_id, owner=owner)

        yield _sse({
            "type": "swarm_done",
            "execution_id": execution.id,
            "total_tokens": execution.total_tokens,
            "duration_ms": execution.duration_ms,
            "workers_activated": execution.workers_activated,
            "workers_skipped": execution.workers_skipped,
            "status": execution.status,
        })
        yield "data: [DONE]\n\n"

        # Persist run to DB (fire-and-forget)
        try:
            await self._persist_run(execution)
        except Exception:
            logger.warning("Failed to persist swarm run", exc_info=True)

    # -----------------------------------------------------------------------
    # Planning
    # -----------------------------------------------------------------------

    async def _plan(
        self,
        swarm: SwarmDefinition,
        user_query: str,
        endpoint_url: str,
        model: str,
        headers: Optional[Dict[str, str]],
        temperature: float,
        context_length: int,
    ) -> Dict[str, Any]:
        """Have the master agent analyse the query and select workers."""
        from src.llm_core import stream_llm

        # Build worker catalogue
        catalogue_lines = []
        for w in swarm.workers:
            desc = w.description or w.name
            catalogue_lines.append(f"- **{w.name}** (slug: `{w.slug}`): {desc}")
        catalogue = "\n".join(catalogue_lines)

        system = _PLANNING_SYSTEM.format(
            master_name=swarm.master.name,
            swarm_name=swarm.name,
            worker_catalogue=catalogue,
            max_workers=min(MAX_WORKERS_PER_RUN, len(swarm.workers)),
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_query},
        ]

        # Use the master's model/endpoint if overridden
        plan_model = swarm.master.model or model
        plan_endpoint = swarm.master.endpoint_url or endpoint_url

        # Collect the full response
        full_text = ""
        async for chunk in stream_llm(
            endpoint_url=plan_endpoint,
            model=plan_model,
            messages=messages,
            headers=headers,
            temperature=temperature,
            max_tokens=PLANNING_MAX_TOKENS,
            context_length=context_length,
        ):
            full_text += chunk

        # Parse JSON from the response
        plan_data = self._parse_plan_json(full_text, swarm)
        return plan_data

    def _parse_plan_json(self, text: str, swarm: SwarmDefinition) -> Dict[str, Any]:
        """Extract and validate the JSON plan from the master's response."""
        import re

        # Try to find JSON in the response (with or without markdown fences)
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            raise ValueError(f"Master agent did not return valid JSON plan. Response: {text[:500]}")

        try:
            raw = json.loads(json_match.group())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in plan: {exc}. Response: {text[:500]}") from exc

        valid_slugs = set(swarm.worker_slugs())

        # Build SwarmTask objects
        tasks: List[SwarmTask] = []
        slug_to_task_id: Dict[str, str] = {}

        for item in raw.get("tasks", []):
            slug = item.get("worker", "")
            if slug not in valid_slugs:
                logger.warning("Plan references unknown worker slug: %s", slug)
                continue

            task = SwarmTask(
                role_slug=slug,
                prompt=item.get("task", ""),
            )
            slug_to_task_id[slug] = task.id
            tasks.append(task)

        # Resolve dependencies (slug → task ID)
        for task in tasks:
            raw_task = next(
                (t for t in raw.get("tasks", []) if t.get("worker") == task.role_slug),
                {},
            )
            dep_slugs = raw_task.get("depends_on", [])
            task.dependencies = [
                slug_to_task_id[s] for s in dep_slugs
                if s in slug_to_task_id
            ]

        # Skipped workers
        skipped = []
        for item in raw.get("skipped", []):
            slug = item.get("worker", "")
            if slug in valid_slugs:
                skipped.append({"worker": slug, "reason": item.get("reason", "")})

        # Add any workers not mentioned to skipped
        mentioned = {t.role_slug for t in tasks} | {s["worker"] for s in skipped}
        for slug in valid_slugs - mentioned:
            skipped.append({"worker": slug, "reason": "Not selected by master"})

        return {
            "reasoning": raw.get("reasoning", ""),
            "tasks": tasks,
            "skipped": skipped,
        }

    # -----------------------------------------------------------------------
    # Worker dispatch
    # -----------------------------------------------------------------------

    async def _run_single_worker(
        self,
        swarm: SwarmDefinition,
        task: SwarmTask,
        execution: SwarmExecution,
        shared_memory: SwarmMemory,
        endpoint_url: str,
        model: str,
        session_id: str,
        owner: Optional[str],
        headers: Optional[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        context_length: int,
        disabled_tools: Optional[Set[str]],
        user_query: str,
    ) -> List[str]:
        """Run a single worker and collect its SSE events."""
        role = swarm.worker_by_slug(task.role_slug)
        if not role:
            task.status = TaskStatus.FAILED
            task.result = f"Unknown role: {task.role_slug}"
            return [_sse({
                "type": "worker_failed",
                "worker": task.role_slug,
                "error": task.result,
            })]

        events: List[str] = []
        try:
            async for event in run_worker(
                role=role,
                task=task,
                endpoint_url=endpoint_url,
                model=model,
                session_id=session_id,
                owner=owner,
                shared_memory=shared_memory,
                user_query=user_query,
                headers=headers,
                temperature=temperature,
                max_tokens=max_tokens,
                context_length=context_length,
                disabled_tools=disabled_tools,
            ):
                events.append(event)
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.result = f"Error: {exc}"
            task.completed_at = time.time()
            events.append(_sse({
                "type": "worker_failed",
                "worker": role.name,
                "worker_slug": role.slug,
                "error": str(exc),
            }))

        # Retry on failure if retries remain
        if task.status == TaskStatus.FAILED and MAX_RETRIES > 0:
            logger.info("Retrying failed worker %s", role.slug)
            task.status = TaskStatus.PENDING
            task.result = None
            retry_events: List[str] = []
            try:
                async for event in run_worker(
                    role=role,
                    task=task,
                    endpoint_url=endpoint_url,
                    model=model,
                    session_id=session_id,
                    owner=owner,
                    shared_memory=shared_memory,
                    user_query=user_query,
                    headers=headers,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    context_length=context_length,
                    disabled_tools=disabled_tools,
                ):
                    retry_events.append(event)
                events.extend(retry_events)
            except Exception as exc:
                logger.warning("Retry also failed for worker %s", role.slug)
                task.status = TaskStatus.FAILED
                task.result = f"Retry failed: {exc}"
                task.completed_at = time.time()
                task.metrics = {"error": str(exc)}

        return events

    # -----------------------------------------------------------------------
    # Merging
    # -----------------------------------------------------------------------

    async def _merge(
        self,
        swarm: SwarmDefinition,
        execution: SwarmExecution,
        shared_memory: SwarmMemory,
        user_query: str,
        endpoint_url: str,
        model: str,
        headers: Optional[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        context_length: int,
    ) -> AsyncGenerator[str, None]:
        """Master synthesises all worker outputs into the final response."""
        from src.llm_core import stream_llm

        system = _MERGE_SYSTEM.format(
            master_name=swarm.master.name,
            swarm_name=swarm.name,
        )

        # Build the worker outputs section
        worker_outputs = []
        for task in execution.tasks:
            if task.status == TaskStatus.DONE and task.result:
                role = swarm.worker_by_slug(task.role_slug)
                name = role.name if role else task.role_slug
                worker_outputs.append(f"### {name}\n**Task:** {task.prompt}\n\n{task.result}")

        if not worker_outputs:
            yield _sse({"delta": "No worker outputs to synthesise."})
            return

        user_content = (
            f"## Original User Request\n{user_query}\n\n"
            f"## Team Outputs\n\n" + "\n\n---\n\n".join(worker_outputs)
        )

        merge_model = swarm.master.model or model
        merge_endpoint = swarm.master.endpoint_url or endpoint_url

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        async for chunk in stream_llm(
            endpoint_url=merge_endpoint,
            model=merge_model,
            messages=messages,
            headers=headers,
            temperature=temperature,
            max_tokens=max_tokens,
            context_length=context_length,
        ):
            yield _sse({"delta": chunk})

    def _fallback_merge(self, execution: SwarmExecution) -> str:
        """Simple concatenation fallback when the master merge LLM call fails."""
        parts = []
        for task in execution.tasks:
            if task.status == TaskStatus.DONE and task.result:
                parts.append(f"## {task.role_slug}\n{task.result}")
        return "\n\n---\n\n".join(parts) if parts else "Swarm execution completed but produced no output."

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    async def _persist_run(self, execution: SwarmExecution) -> None:
        """Save the execution record to the database."""
        try:
            from core.database import SessionLocal, SwarmRun
        except ImportError:
            logger.debug("SwarmRun model not available yet — skipping persistence")
            return

        db = SessionLocal()
        try:
            run = SwarmRun(
                id=execution.id,
                swarm_id=execution.swarm_id,
                session_id=execution.session_id or None,
                owner=execution.owner,
                user_query=execution.user_query,
                master_plan=json.dumps({
                    "reasoning": execution.master_plan,
                    "skipped": execution.skipped,
                }),
                worker_results=json.dumps([t.to_dict() for t in execution.tasks]),
                final_response=execution.final_response,
                status=execution.status,
                total_tokens=execution.total_tokens,
                duration_ms=execution.duration_ms,
                workers_activated=execution.workers_activated,
                workers_skipped=execution.workers_skipped,
            )
            db.add(run)
            db.commit()
            logger.info("Persisted swarm run %s", execution.id)
        except Exception:
            db.rollback()
            logger.warning("Failed to persist swarm run %s", execution.id, exc_info=True)
        finally:
            db.close()

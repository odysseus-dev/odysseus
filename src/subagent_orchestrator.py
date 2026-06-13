"""Subagent orchestrator — multiagent slice-1.

Spec: docs/superpowers/specs/2026-06-12-odysseus-multiagent-orchestration-design.md.

Coordinator LLM emits a ``spawn_agent`` tool call; this module resolves each
entry to a concrete binding (persona SOUL.md + scoped tools + derived
``agent:{human}/{name}`` owner id), enforces depth / parallel / scoping
bounds, runs nested agent loops (sequentially or bounded-parallel), and
joins structured per-entry results into one tool result.

Safety invariants enforced here:
- depth cap: spawning refused at ``depth >= agent_max_depth``; nested loops
  always get ``spawn_agent`` disabled when their children would exceed it.
- parallel cap: ``agent_max_parallel`` semaphore (single-GPU pool).
- tool scoping: a subagent's tools are the binding's tools intersected with
  the coordinator's available set (never wider than the parent).
- identity: human owner inherited at spawn, never elevated (see
  services.agents.profile.derive_owner); persona text is injected as a
  separate user-role message tagged ``trusted: False``.
- failure isolation: one entry's failure never aborts siblings; the
  coordinator always sees partial results.
"""
import asyncio
import json
import logging
import re
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable, Optional

from services.agents.profile import ProfileError, resolve_binding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Run context — how spawn_agent (executed deep inside tool dispatch, which
# only sees owner/session_id) learns the coordinator's endpoint/model/depth.
# stream_agent_loop seeds it at loop start; _default_run_loop re-seeds one
# level deeper around each nested run and restores on the way out.
# ---------------------------------------------------------------------------

_run_ctx: ContextVar[Optional[dict]] = ContextVar("subagent_run_ctx",
                                                  default=None)


def seed_run_context(*, endpoint_url: str, model: str,
                     headers: Optional[dict], owner: Optional[str],
                     session_id: Optional[str],
                     coordinator_tools: Optional[set] = None):
    """Seed (or refresh) the run context for one agent loop.

    Depth and the ORIGINAL human owner survive re-seeding by nested loops:
    the human is captured at the top level (where owner is the human) and
    never replaced by a derived agent id.
    """
    cur = _run_ctx.get()
    human = cur["human_owner"] if cur else (
        None if (owner or "").startswith("agent:") else owner)
    return _run_ctx.set({
        "endpoint_url": endpoint_url, "model": model, "headers": headers,
        "session_id": session_id,
        "depth": cur["depth"] if cur else 0,
        "human_owner": human,
        "coordinator_tools": (coordinator_tools
                              if coordinator_tools is not None
                              else (cur or {}).get("coordinator_tools")),
    })


def get_run_context() -> Optional[dict]:
    return _run_ctx.get()


def reset_run_context(token) -> None:
    _run_ctx.reset(token)

# A loop runner executes one nested agent run and returns the final text.
# Injected for tests; the default drains src.agent_loop.stream_agent_loop.
LoopRunner = Callable[..., Awaitable[str]]


def _max_depth() -> int:
    from src.settings import get_setting
    return int(get_setting("agent_max_depth", 2))


def _max_parallel() -> int:
    from src.settings import get_setting
    return max(1, int(get_setting("agent_max_parallel", 2)))


async def _default_run_loop(*, binding: dict, messages: list,
                            endpoint_url: str, model: str,
                            headers: Optional[dict],
                            tools: set, disable_spawn: bool,
                            session_id: Optional[str]) -> str:
    """Drain a nested stream_agent_loop into the final assistant text."""
    from src.agent_loop import stream_agent_loop
    disabled = {"spawn_agent"} if disable_spawn else set()
    chunks: list[str] = []
    # Nested run context: one level deeper, same human, child's tool set
    # becomes the coordinator set for any further (allowed) spawns.
    cur = _run_ctx.get() or {}
    token = _run_ctx.set({**cur,
                          "depth": int(cur.get("depth", 0)) + 1,
                          "session_id": session_id,
                          "coordinator_tools": set(tools)})
    try:
        agen = stream_agent_loop(
            endpoint_url=endpoint_url,
            model=binding.get("model") or model,
            messages=messages,
            headers=headers,
            owner=binding["owner"],
            relevant_tools=set(tools),
            disabled_tools=disabled,
            session_id=session_id,
        )
        async for ev in agen:
            if not ev.startswith("data: "):
                continue
            payload = ev[len("data: "):].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("delta"), str):
                chunks.append(data["delta"])
    finally:
        _run_ctx.reset(token)
    text = "".join(chunks)
    # strip reasoning blocks so the coordinator joins clean output
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _subagent_messages(binding: dict) -> list:
    """Nested conversation: persona identity as UNTRUSTED user content
    (same treatment agent_loop gives skills/documents), then the task."""
    msgs = []
    soul = (binding.get("soul") or "").strip()
    if soul:
        msgs.append({
            "role": "user",
            "content": f"[persona]\n{soul}",
            "metadata": {"trusted": False, "kind": "persona"},
        })
    msgs.append({"role": "user", "content": binding["task"]})
    return msgs


async def _run_one(binding: dict, *, depth: int, persist: bool,
                   human_owner: Optional[str],
                   parent_session_id: Optional[str],
                   endpoint_url: str, model: str, headers: Optional[dict],
                   coordinator_tools: Optional[set],
                   run_loop: LoopRunner) -> dict:
    """Execute one resolved binding; never raises (structured result)."""
    tools = set(binding["tools"])
    if coordinator_tools is not None:
        tools &= set(coordinator_tools)   # scoping intersection: never wider

    child_session_id = None
    if persist or binding.get("persist"):
        child_session_id = f"subagent-{uuid.uuid4().hex[:12]}"
        try:
            from core.models import get_session_manager_instance
            sm = get_session_manager_instance()
        except Exception:
            sm = None
        if sm is not None:
            try:
                sm.create_subagent_session(
                    child_session_id, f"subagent: {binding['name']}",
                    endpoint_url, binding.get("model") or model,
                    agent_owner=binding["owner"],
                    human_owner=human_owner or "local",
                    parent_session_id=parent_session_id or "")
            except Exception as e:
                logger.warning(f"subagent session persist failed: {e}")
                child_session_id = None

    disable_spawn = depth + 1 >= _max_depth()
    try:
        output = await run_loop(
            binding=binding, messages=_subagent_messages(binding),
            endpoint_url=endpoint_url, model=model, headers=headers,
            tools=tools, disable_spawn=disable_spawn,
            session_id=child_session_id)
        return {"name": binding["name"], "owner": binding["owner"],
                "status": "ok", "output": output,
                "session_id": child_session_id,
                # subagent output returns to the coordinator as DATA
                "trusted": False}
    except asyncio.TimeoutError:
        return {"name": binding["name"], "owner": binding["owner"],
                "status": "error", "error": "subagent timed out",
                "session_id": child_session_id, "trusted": False}
    except Exception as e:
        logger.warning(f"subagent {binding['name']} failed: {e}")
        return {"name": binding["name"], "owner": binding["owner"],
                "status": "error", "error": str(e),
                "session_id": child_session_id, "trusted": False}


async def spawn(args: dict, *, human_owner: Optional[str],
                endpoint_url: str, model: str,
                headers: Optional[dict] = None,
                parent_session_id: Optional[str] = None,
                coordinator_tools: Optional[set] = None,
                depth: int = 0,
                run_loop: Optional[LoopRunner] = None,
                data_dir=None) -> dict:
    """Entry point for the spawn_agent tool. Always returns a dict result
    (refusals and validation errors included), never raises."""
    if depth >= _max_depth():
        return {"status": "refused",
                "error": f"agent depth cap reached ({_max_depth()}); "
                         "spawn_agent is not available at this depth",
                "results": []}

    entries = args.get("agents")
    if not isinstance(entries, list) or not entries:
        return {"status": "error",
                "error": "spawn_agent needs a non-empty 'agents' list",
                "results": []}
    mode = args.get("mode") or "auto"
    if mode not in ("auto", "sequential", "parallel"):
        return {"status": "error",
                "error": f"unknown mode {mode!r}", "results": []}
    persist_default = bool(args.get("persist", False))

    bindings, errors = [], []
    for i, entry in enumerate(entries):
        try:
            bindings.append(resolve_binding(entry, human_owner,
                                            data_dir=data_dir))
        except ProfileError as e:
            errors.append({"name": f"entry[{i}]", "status": "error",
                           "error": str(e), "trusted": False})
    if not bindings:
        return {"status": "error", "error": "no spawnable entries",
                "results": errors}

    runner = run_loop or _default_run_loop
    run_kwargs = dict(depth=depth, persist=persist_default,
                      human_owner=human_owner,
                      parent_session_id=parent_session_id,
                      endpoint_url=endpoint_url, model=model, headers=headers,
                      coordinator_tools=coordinator_tools, run_loop=runner)

    parallel = (mode == "parallel") or (mode == "auto" and len(bindings) > 1)
    if parallel:
        sem = asyncio.Semaphore(_max_parallel())

        async def _bounded(b):
            async with sem:
                return await _run_one(b, **run_kwargs)

        results = list(await asyncio.gather(
            *(_bounded(b) for b in bindings)))
    else:
        results = [await _run_one(b, **run_kwargs) for b in bindings]

    results.extend(errors)
    ok = sum(1 for r in results if r["status"] == "ok")
    return {"status": "ok" if ok else "error",
            "mode": "parallel" if parallel else "sequential",
            "depth": depth, "completed": ok,
            "failed": len(results) - ok, "results": results}

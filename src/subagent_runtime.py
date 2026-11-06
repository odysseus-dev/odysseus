"""subagent_runtime.py — parallel in-process subagents for the Odysseus agent loop.

A subagent is a plain `stream_agent_loop` run with a fresh message list and its
own SSE sink, so nothing it says reaches the parent's stream or chat history.
Depth is capped at MAX_DEPTH: the main loop may delegate, a subagent may not.

The runtime handle lives in a ContextVar because the tool dispatcher's `ctx`
does not carry the endpoint, model or headers. `stream_agent_loop` binds it on
entry, which makes it request-scoped (each SSE request runs in its own task
context).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_DEPTH = 1
MAX_PARALLEL = 4
MAX_ROUNDS = 60
DEFAULT_TIMEOUT_SEC = 900
_TEXT_LIMIT = 6000

# Tools a subagent must never get: delegation (no fan-out recursion), user
# prompts (nobody is listening), teacher escalation (loops back to the parent).
_BLOCKED_FOR_SUBAGENTS = frozenset({"delegate_subagent", "ask_user", "ask_teacher"})

_CTX: ContextVar[Optional[Dict[str, Any]]] = ContextVar("odysseus_subagent_ctx", default=None)

# Registro efímero para la UI de subagentes (se pierde al reiniciar; no es historial).
_RUNS: Dict[str, Dict[str, Any]] = {}
_RUNS_ORDER: List[str] = []
_MAX_RUNS = 40


_LOADED = False


def _persist_path() -> str:
    from src.constants import DATA_DIR
    return str(DATA_DIR / "subagent_runs.jsonl")


def _load_persisted() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        import os
        path = _persist_path()
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-_MAX_RUNS:]
        for line in lines:
            try:
                entry = json.loads(line)
            except (TypeError, ValueError):
                continue
            rid = entry.get("id")
            if not rid:
                continue
            entry["persisted"] = True
            _RUNS[rid] = entry
            _RUNS_ORDER.append(rid)
    except Exception:
        logger.debug("no se pudo cargar historial de subagentes", exc_info=True)


def _record(run_id: str, **fields: Any) -> None:
    entry = _RUNS.setdefault(run_id, {"id": run_id})
    entry.update(fields)
    if run_id not in _RUNS_ORDER:
        _RUNS_ORDER.append(run_id)
    while len(_RUNS_ORDER) > _MAX_RUNS:
        old = _RUNS_ORDER.pop(0)
        _RUNS.pop(old, None)
    if fields.get("status") in ("done", "error"):
        try:
            with open(_persist_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("no se pudo persistir subagente", exc_info=True)


def snapshot(limit: int = 20) -> List[Dict[str, Any]]:
    _load_persisted()
    ids = [i for i in reversed(_RUNS_ORDER) if i in _RUNS][: max(1, int(limit))]
    return [dict(_RUNS[i]) for i in ids]


def bind(endpoint_url: str, model: str, headers: Optional[dict] = None, *,
         depth: int = 0, owner: Optional[str] = None,
         workspace: Optional[str] = None,
         disabled_tools: Optional[set] = None,
         fallbacks: Optional[list] = None):
    """Publish the runtime handle for the current task context."""
    return _CTX.set({
        "endpoint_url": endpoint_url,
        "model": model,
        "headers": headers,
        "depth": int(depth or 0),
        "owner": owner,
        "workspace": workspace,
        "disabled_tools": set(disabled_tools or ()),
        "fallbacks": fallbacks,
    })


def current() -> Optional[Dict[str, Any]]:
    return _CTX.get()


def reset(token) -> None:
    try:
        _CTX.reset(token)
    except (LookupError, ValueError):
        pass


def can_delegate() -> bool:
    """True only from a bound loop that is still above the depth cap."""
    ctx = current()
    return bool(
        ctx
        and ctx.get("endpoint_url")
        and ctx.get("model")
        and int(ctx.get("depth", 0)) < MAX_DEPTH
    )


async def with_delegation(agen, endpoint_url: str, model: str,
                          headers: Optional[dict] = None, **kwargs):
    """Yield from `agen` with the runtime handle bound for its duration.

    Wrapping the generator (instead of binding at the call site) keeps the
    binding alive exactly as long as the parent loop runs and guarantees the
    reset even when the stream raises or the client disconnects.
    """
    token = bind(endpoint_url, model, headers, **kwargs)
    try:
        async for item in agen:
            yield item
    finally:
        reset(token)


def _parse_event(raw: Any) -> Optional[dict]:
    if not isinstance(raw, str):
        return None
    payload = raw.strip()
    if payload.startswith("data:"):
        payload = payload[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        event = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def _system_prompt(label: str, override: Optional[str]) -> str:
    if override:
        return str(override)
    return (
        f"You are subagent '{label}'. Work only on the instruction you were given; "
        "you do not see any other conversation. You cannot ask the user questions and "
        "cannot delegate further. Prefer running a command and reading its real output "
        "over guessing. Finish with a short factual report: what you did, the exact "
        "commands you ran, and their result. State failures plainly."
    )


async def _run_one(index: int, task: dict, parent: Dict[str, Any], timeout: float) -> dict:
    from src.agent_loop import stream_agent_loop  # local import: avoids a cycle

    label = str(task.get("label") or f"subagent-{index + 1}")
    instruction = str(task.get("instruction") or "").strip()
    started = time.monotonic()
    run_id = f"{label}-{int(started * 1000)}"
    _record(run_id, label=label, status="running", started=started,
            model=str(parent.get("model") or ""), ok=None, seconds=None,
            tools=0, error="")

    if not instruction:
        _record(run_id, status="error", ok=False, seconds=0.0, error="empty instruction")
        return {"label": label, "ok": False, "seconds": 0.0, "text": "",
                "tools": [], "errors": ["empty instruction"]}

    disabled = set(parent.get("disabled_tools") or ()) | _BLOCKED_FOR_SUBAGENTS
    messages = [
        {"role": "system", "content": _system_prompt(label, task.get("system"))},
        {"role": "user", "content": instruction},
    ]

    text: List[str] = []
    tools: List[dict] = []
    errors: List[str] = []
    usage: Dict[str, Any] = {}
    files: List[str] = []

    async def drain() -> None:
        async for raw in stream_agent_loop(
            parent["endpoint_url"],
            parent["model"],
            messages,
            headers=parent.get("headers"),
            max_rounds=MAX_ROUNDS,
            session_id=None,
            disabled_tools=disabled,
            owner=parent.get("owner"),
            workspace=parent.get("workspace"),
            fallbacks=parent.get("fallbacks"),
        ):
            event = _parse_event(raw)
            if not event:
                continue
            delta = event.get("delta")
            if isinstance(delta, str):
                # Los modelos de razonamiento mandan el scratchpad como delta
                # con thinking=True. Sin filtrarlo, el subagente devuelve su
                # monologo interno en vez de la respuesta final.
                if not event.get("thinking"):
                    text.append(delta)
                continue
            kind = event.get("type")
            if kind == "tool_start":
                name = event.get("tool") or event.get("name")
                if name:
                    # `tool_start` solo trae tool/full_command/command; sin este
                    # respaldo el informe muestra args vacios aunque la
                    # herramienta se haya ejecutado con su comando.
                    arg_blob = (
                        event.get("args")
                        or event.get("input")
                        or event.get("full_command")
                        or event.get("command")
                        or ""
                    )
                    tools.append({"tool": str(name), "args": str(arg_blob)[:400]})
                    if any(k in str(name) for k in ("write_file", "edit_file", "apply_patch", "create_file", "str_replace")):
                        import re as _re
                        for match in _re.finditer(r'["\'](?:path|file_path|filename)["\']\s*:\s*["\']([^"\']+)["\']', str(arg_blob)):
                            files.append(match.group(1))
            elif kind == "metrics":
                data = event.get("data")
                if isinstance(data, dict):
                    usage.update(data)
            elif kind in ("error", "tool_error"):
                errors.append(str(event.get("message") or event.get("error") or kind))

    try:
        await asyncio.wait_for(drain(), timeout)
    except asyncio.TimeoutError:
        errors.append(f"timeout after {timeout:g}s")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a broken subagent must not kill its siblings
        errors.append(f"{type(exc).__name__}: {exc}")
        logger.warning("subagent %s failed: %s", label, exc)

    result = {
        "label": label,
        "ok": not errors,
        "seconds": round(time.monotonic() - started, 2),
        "text": "".join(text).strip()[:_TEXT_LIMIT],
        "tools": tools[-40:],
        "errors": errors,
        "usage": usage,
        "files": sorted(set(files))[:40],
    }
    _record(run_id, status="done" if result["ok"] else "error", ok=result["ok"],
            seconds=result["seconds"], tools=len(result["tools"]),
            error="; ".join(errors)[:300],
            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
            cached_tokens=usage.get("cached_input_tokens") or usage.get("cached_tokens"),
            files=len(set(files)))
    return result


async def _run_child(index: int, task: dict, parent: Dict[str, Any], timeout: float) -> dict:
    """Run one subagent with depth bumped, isolated to its own task context."""
    # §25: el router decide nivel, clase de modelo y esfuerzo con los rasgos del
    # enunciado. Decidir y no aplicarlo sería teatro, así que la clase se mapea a
    # un modelo de la matriz y, si está configurado y es alcanzable, el hijo lo usa;
    # si no, se queda con el del padre. El router nunca tumba una delegación.
    try:
        from src import agent_router, agent_router_ledger, agent_router_policy

        try:
            _recomendaciones = agent_router_ledger.recomendar_cacheado()
        except Exception:
            _recomendaciones = None
        _plan = agent_router_policy.planificar(
            str(task.get("instruction") or ""),
            criterio=str(task.get("criterio") or task.get("instruction") or ""),
            esfuerzo_pedido=task.get("effort"),
            preset=str(parent.get("preset") or ""),
            padre_clase=str(task.get("parent_clase") or ""),
            presupuesto_usd=task.get("presupuesto_usd"),
            recomendaciones=_recomendaciones,
        )
        _decision = agent_router.elegir(
            str(task.get("instruction") or ""),
            criterio=str(task.get("criterio") or task.get("instruction") or ""),
            hay_test=bool(task.get("hay_test")),
        )
        if _plan.delegar:
            _modelo = agent_router.modelo_de_clase(_plan.clase,
                                                   alcanzables=[parent.get("model")])
            if _modelo:
                parent = {**parent, "model": _modelo}
            task = {**task, "route": _plan.como_dict()}
            agent_router.anotar(
                agent_router.Decision(_plan.delegar, _plan.banda, _plan.clase,
                                      _plan.esfuerzo, _plan.motivo),
                tarea=str(task.get("label") or ""),
                tipo=_plan.tipo,
            )
    except Exception as exc:  # pragma: no cover - el router no puede romper nada
        logger.warning("router: %s", exc)
    token = bind(
        parent["endpoint_url"],
        parent["model"],
        parent.get("headers"),
        depth=int(parent.get("depth", 0)) + 1,
        owner=parent.get("owner"),
        workspace=parent.get("workspace"),
        disabled_tools=parent.get("disabled_tools"),
        fallbacks=parent.get("fallbacks"),
    )
    try:
        return await _run_one(index, task, current() or parent, timeout)
    finally:
        reset(token)


async def run_tasks(tasks: List[dict], *, parallel: bool = True,
                    timeout: float = DEFAULT_TIMEOUT_SEC,
                    max_parallel: int = MAX_PARALLEL) -> List[dict]:
    parent = current()
    if not parent:
        raise RuntimeError("no subagent runtime bound; call this from inside the agent loop")

    clean = [t for t in tasks if isinstance(t, dict) and str(t.get("instruction") or "").strip()]
    clean = clean[:max(1, int(max_parallel))]
    if not clean:
        return []

    if not parallel or len(clean) == 1:
        return [await _run_child(i, task, parent, timeout) for i, task in enumerate(clean)]

    sem = asyncio.Semaphore(max(1, int(max_parallel)))

    async def guarded(i: int, task: dict) -> dict:
        async with sem:
            return await _run_child(i, task, parent, timeout)

    return list(await asyncio.gather(*(guarded(i, t) for i, t in enumerate(clean))))

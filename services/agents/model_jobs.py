"""Bounded model-job executor. No conversation, workspace, or delegation."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable

PROVENANCE_KEY = "_provenance"
_SECRET_AUDIT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "virtual_key",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "ninerouter_key",
        "nine_router_key",
        "openhands_conversation_id",
        "conversation_id",
    }
)
_TITLE_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")
_CASUAL_TITLE_WORDS = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "thanks",
        "thank",
        "you",
        "ty",
        "ok",
        "okay",
        "yes",
        "no",
    }
)


class InvalidArchetype(Exception):
    """Archetype is not a bounded model job or produced an invalid result."""


class ModelJobFailed(Exception):
    """Worker or 9router failed. Callers must fail closed."""


@dataclass(frozen=True)
class ModelJobResult:
    output: dict[str, Any]
    owner: str
    conversation_id: None
    audit: dict[str, Any]


class ModelJobExecutor:
    def __init__(self, invoke: Callable[..., Any] | None = None, max_retries: int = 1) -> None:
        if invoke is None:
            from src.ai_interaction import invoke_structured_model

            invoke = invoke_structured_model
        self.invoke = invoke
        self.max_retries = max_retries

    def execute(self, archetype: Any, payload: dict[str, Any], owner: str) -> ModelJobResult:
        kind = getattr(archetype, "execution_kind", None)
        kind_value = getattr(kind, "value", kind)
        if kind_value not in {"model-job", None} and str(kind_value) != "model-job":
            if str(kind_value) == "agent":
                raise InvalidArchetype("agent archetype cannot run as a model job")
        tools = list(getattr(archetype, "tools", None) or [])
        if tools:
            raise InvalidArchetype("model jobs cannot use arbitrary tools")
        if getattr(archetype, "conversation_policy", None):
            raise InvalidArchetype("model jobs cannot open conversations")
        if getattr(archetype, "workspace_policy", None):
            raise InvalidArchetype("model jobs cannot mount workspaces")
        if getattr(archetype, "delegation_policy", None):
            raise InvalidArchetype("model jobs cannot delegate")
        last_error: Exception | None = None
        output: Any = None
        attempts = max(self.max_retries, 1)
        for _ in range(attempts):
            try:
                output = self.invoke(payload, archetype=archetype, owner=owner)
                last_error = None
                break
            except Exception as exc:  # transient only; next attempt or raise
                last_error = exc
        if last_error is not None:
            raise last_error
        if not isinstance(output, dict):
            raise InvalidArchetype("schema failure: result must be an object")
        output = dict(output)
        provenance = output.pop(PROVENANCE_KEY, None)
        if not isinstance(provenance, dict):
            provenance = {}
        return ModelJobResult(
            output=output,
            owner=owner,
            conversation_id=None,
            audit=public_audit(
                {
                    "archetype": getattr(archetype, "id", "unknown"),
                    "version": getattr(archetype, "version", None),
                    "token_limit": getattr(archetype, "token_limit", None),
                    "timeout_seconds": getattr(archetype, "timeout_seconds", None),
                    **provenance,
                }
            ),
        )


def public_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """Drop secret-shaped keys and values from job audit."""
    cleaned: dict[str, Any] = {}
    for key, value in audit.items():
        if str(key).lower() in _SECRET_AUDIT_KEYS:
            continue
        if isinstance(value, str) and (
            value.lower().startswith("sk-") or "bearer " in value.lower()
        ):
            continue
        cleaned[key] = value
    return cleaned


def session_title_heuristic(text: str) -> str | None:
    """Return a 3-6 word title when the first message is already enough."""
    words = _TITLE_WORD_RE.findall(" ".join(str(text or "").strip().strip("\"'").split()))
    if len(words) < 3:
        return None
    if all(word.lower() in _CASUAL_TITLE_WORDS for word in words):
        return None
    title = " ".join(words[:6])
    if not title or len(title) >= 80:
        return None
    return title


def bounded_archetype(job_id: str, **overrides: Any) -> SimpleNamespace:
    """Build a tools-free model-job archetype for a typed product job."""
    data: dict[str, Any] = {
        "id": job_id,
        "version": 1,
        "execution_kind": "model-job",
        "input_schema": f"{job_id}InputV1",
        "result_schema": f"{job_id}ResultV1",
        "token_limit": 4096,
        "timeout_seconds": 60,
        "temperature": 0.3,
        "tools": [],
        "conversation_policy": None,
        "workspace_policy": None,
        "delegation_policy": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def archetype_to_dict(archetype: Any) -> dict[str, Any]:
    """Serialize an archetype for the worker HTTP contract."""
    kind = getattr(archetype, "execution_kind", "model-job")
    kind_value = getattr(kind, "value", kind)
    return {
        "id": getattr(archetype, "id", "unknown"),
        "version": getattr(archetype, "version", 1),
        "execution_kind": str(kind_value or "model-job"),
        "input_schema": getattr(archetype, "input_schema", ""),
        "result_schema": getattr(archetype, "result_schema", ""),
        "token_limit": getattr(archetype, "token_limit", None),
        "timeout_seconds": getattr(archetype, "timeout_seconds", 60),
        "temperature": getattr(archetype, "temperature", None),
        "tools": list(getattr(archetype, "tools", None) or []),
        "conversation_policy": getattr(archetype, "conversation_policy", None),
        "workspace_policy": getattr(archetype, "workspace_policy", None),
        "delegation_policy": getattr(archetype, "delegation_policy", None),
    }


def submit_model_job(
    archetype: Any,
    payload: dict[str, Any],
    owner: str,
    *,
    invoke: Callable[..., Any] | None = None,
) -> ModelJobResult:
    """Run one owner-scoped typed job through ModelJobExecutor."""
    return ModelJobExecutor(invoke=invoke).execute(archetype, payload, owner)


def run_compare_jobs(
    panes: Iterable[dict[str, Any]],
    owner: str,
    *,
    invoke: Callable[..., Any] | None = None,
) -> list[ModelJobResult]:
    """Submit compare panes as parallel bounded jobs with per-pane provenance."""
    pending = [dict(pane) for pane in panes]
    archetype = bounded_archetype("compare-pane")

    def _one(pane: dict[str, Any]) -> ModelJobResult:
        return submit_model_job(archetype, pane, owner, invoke=invoke)

    if len(pending) <= 1:
        return [_one(pane) for pane in pending]
    with ThreadPoolExecutor(max_workers=len(pending)) as pool:
        return list(pool.map(_one, pending))


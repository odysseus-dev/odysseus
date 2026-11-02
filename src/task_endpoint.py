"""Shared resolver for background-task AI endpoints."""

from src.endpoint_resolver import (
    resolve_endpoint,
    resolve_utility_fallback_candidates,
    same_endpoint_base as _same_endpoint_base,
)
from src.llm_core import llm_call_async_with_fallback
from src.interactive_gate import wait_for_interactive_quiet


def resolve_task_endpoint(fallback_url=None, fallback_model=None, fallback_headers=None, owner=None):
    """Return (endpoint_url, model, headers) for background tasks.

    Reads task_endpoint_id / task_model from admin settings.
    Falls back to the provided values when the setting is empty or the
    endpoint cannot be resolved.
    """
    return resolve_endpoint("task", fallback_url, fallback_model, fallback_headers, owner=owner)


def resolve_task_candidates(
    fallback_url=None,
    fallback_model=None,
    fallback_headers=None,
    override_url=None,
    override_model=None,
    override_headers=None,
    owner=None,
):
    """Return ordered background-task LLM candidates.

    Order:
    1. configured Background Tasks endpoint/model, or caller fallback
    2. Utility endpoint/model
    3. Default endpoint/model
    4. Utility fallback chain
    """
    candidates = []

    def _append(url, model, headers):
        if not url or not model:
            return
        key = (url, model)
        if any((u, m) == key for u, m, _ in candidates):
            return
        candidates.append((url, model, headers or {}))

    if override_url and override_model:
        try:
            from src.database import SessionLocal
            from src.endpoint_resolver import (
                build_chat_url, build_headers, resolve_endpoint_runtime,
                resolve_owner_registered_endpoint,
            )
            db = SessionLocal()
            try:
                ep = resolve_owner_registered_endpoint(db, override_url, owner)
                runtime_base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                _append(build_chat_url(runtime_base), override_model, build_headers(api_key, runtime_base))
            finally:
                db.close()
        except Exception:
            # A missing, foreign, disabled or unreadable registration is not
            # authority to contact a caller-provided endpoint or use its key.
            pass
    _append(*resolve_task_endpoint(fallback_url, fallback_model, fallback_headers, owner=owner))
    _append(*resolve_endpoint("utility", owner=owner))
    _append(*resolve_endpoint("default", owner=owner))
    for url, model, headers in resolve_utility_fallback_candidates(owner=owner):
        _append(url, model, headers)
    return candidates


async def task_llm_call_async(
    messages,
    *,
    fallback_url=None,
    fallback_model=None,
    fallback_headers=None,
    override_url=None,
    override_model=None,
    override_headers=None,
    owner=None,
    **kwargs,
):
    """Call the shared background-task LLM candidate chain."""
    resolver_kwargs = {
        "fallback_url": fallback_url,
        "fallback_model": fallback_model,
        "fallback_headers": fallback_headers,
        "owner": owner,
    }
    if override_url is not None:
        resolver_kwargs["override_url"] = override_url
    if override_model is not None:
        resolver_kwargs["override_model"] = override_model
    if override_headers is not None:
        resolver_kwargs["override_headers"] = override_headers
    candidates = resolve_task_candidates(
        **resolver_kwargs,
    )
    if not candidates:
        raise RuntimeError("No LLM endpoint available for background task")
    await wait_for_interactive_quiet("background task LLM")
    kwargs.setdefault("workload", "background")
    if kwargs.get("reasoning_effort") is None:
        kwargs.pop("reasoning_effort", None)
        from src.utility_effort import candidate_effort_factory

        factory = candidate_effort_factory(owner)
        if factory is not None:
            kwargs.setdefault("candidate_request_factory", factory)
    return await llm_call_async_with_fallback(candidates, messages=messages, **kwargs)

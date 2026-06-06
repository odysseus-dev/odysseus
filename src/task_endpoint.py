"""Shared resolver for background-task AI endpoint (auto-naming, memory, sorting)."""

from src.endpoint_resolver import resolve_endpoint


def _resolve_local_llm_router_fallback(
    endpoint_url,
    model,
    headers,
    *,
    owner=None,
    prompt: str = "background utility task",
):
    """Map __auto_stack__ to a concrete local model for background LLM calls."""
    from src.local_llm_router_routing import (
        is_local_llm_router_auto_model,
        resolve_local_llm_router,
    )

    if not is_local_llm_router_auto_model(model) or not (endpoint_url or "").strip():
        return endpoint_url, model, headers
    try:
        res = resolve_local_llm_router(
            prompt=prompt,
            endpoint_url=endpoint_url,
            headers=headers,
            owner=owner,
            mode="chat",
        )
        return res.endpoint_url, res.model, res.headers
    except Exception:
        return endpoint_url, model, headers


def resolve_task_endpoint(fallback_url=None, fallback_model=None, fallback_headers=None, owner=None):
    """Return (endpoint_url, model, headers) for background tasks.

    Reads task_endpoint_id / task_model from admin settings.
    Falls back to the provided values when the setting is empty or the
    endpoint cannot be resolved.
    """
    url, model, headers = resolve_endpoint(
        "task", fallback_url, fallback_model, fallback_headers, owner=owner,
    )
    return _resolve_local_llm_router_fallback(url, model, headers, owner=owner)

"""Reasoning effort for utility-role LLM calls.

One owner for the rule.  Precedence for a utility call:

1. an explicit caller effort (never touched here);
2. the ``utility_reasoning_effort`` setting, sent only to routes on the
   utility chain (the Utility endpoint/model plus its fallbacks, matched by
   ``(url, model)``);
3. the chat session's own effort (``thinking_mode == "effort:<level>"``), when
   the call is made for a chat session and its route is that session's route.

Every level is validated against what the model advertises; a model without
evidence gets nothing.  Calls with no session only ever use (2).
"""
from typing import Optional


def utility_routes(owner: Optional[str] = None) -> set:
    """The ``(url, model)`` routes that make up the utility chain."""
    from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates

    return {
        (url, model)
        for url, model, _headers in [
            resolve_endpoint("utility", owner=owner),
            *resolve_utility_fallback_candidates(owner=owner),
        ]
        if url and model
    }


def configured_effort(owner: Optional[str] = None) -> str:
    """The raw setting, lowercased; empty when unset (the default)."""
    from src.settings import get_user_setting

    return str(get_user_setting("utility_reasoning_effort", owner or "", "") or "").strip().lower()


def fallback_route_efforts(owner: Optional[str] = None) -> dict[tuple[str, str], str]:
    """Configured effort per fallback candidate in ``utility_model_fallbacks``."""
    from src.endpoint_resolver import resolve_endpoint_by_id
    from src.settings import get_user_setting, load_settings

    try:
        settings = load_settings()
        chain = get_user_setting(
            "utility_model_fallbacks",
            owner or "",
            settings.get("utility_model_fallbacks") or [],
        ) or []
    except Exception:
        return {}

    res = {}
    for entry in chain:
        if not isinstance(entry, dict) or "reasoning_effort" not in entry:
            continue
        effort = str(entry.get("reasoning_effort") or "").strip().lower()
        ep = resolve_endpoint_by_id(
            entry.get("endpoint_id", ""),
            entry.get("model", ""),
            owner=owner,
        )
        if ep:
            url, model, _ = ep
            if url and model:
                res[(url, model)] = effort
    return res


def effort_for_route(url: str, model: str, owner: Optional[str] = None) -> Optional[str]:
    """Validated effort for ``(url, model)``, or None when it must not be sent."""
    fb_efforts = fallback_route_efforts(owner)
    if (url, model) in fb_efforts:
        fb_effort = fb_efforts[(url, model)]
        if not fb_effort or fb_effort in {"off", "none", "default"}:
            return None
        from src.chatgpt_subscription import validate_reasoning_effort

        return validate_reasoning_effort(model, fb_effort)

    effort = configured_effort(owner)
    if not effort or (url, model) not in utility_routes(owner):
        return None
    from src.chatgpt_subscription import validate_reasoning_effort

    return validate_reasoning_effort(model, effort)


def session_effort(session) -> Optional[str]:
    """The raw effort level the chat session is set to, or None."""
    mode = str(getattr(session, "thinking_mode", "") or "")
    if not mode.startswith("effort:"):
        return None
    return mode[len("effort:"):].strip().lower() or None


def _is_session_route(url: str, model: str, session) -> bool:
    from src.endpoint_resolver import same_endpoint_base

    return bool(
        model
        and model == getattr(session, "model", None)
        and same_endpoint_base(url, getattr(session, "endpoint_url", None))
    )


def effort_for_call(url: str, model: str, owner: Optional[str] = None, session=None) -> Optional[str]:
    """Effort an owner-aware utility call should send to ``(url, model)``.

    ``session`` is the chat session the call is made for, or None for
    session-less work (scheduled tasks, pollers, auto-sort).  Never raises:
    a lookup failure means "send nothing".
    """
    try:
        raw = configured_effort(owner)
        if raw in {"off", "none", "default"}:
            return None
        effort = effort_for_route(url, model, owner)
        if effort or session is None:
            return effort
        level = session_effort(session)
        if not level or not _is_session_route(url, model, session):
            return None
        from src.chatgpt_subscription import validate_reasoning_effort

        return validate_reasoning_effort(model, level)
    except Exception:
        return None


def candidate_effort_factory(owner=None):
    """Per-candidate factory for `llm_call_async_with_fallback`.

    Returns None when neither the setting nor any fallback effort is configured.
    Otherwise a factory sending the effort only to utility-chain candidates
    (owner-scoped) whose model advertises it.
    """
    raw = configured_effort(owner)
    has_primary = bool(raw and raw not in {"off", "none", "default"})
    fb_efforts = fallback_route_efforts(owner)
    has_fb = any(eff and eff not in {"off", "none", "default"} for eff in fb_efforts.values())
    if not has_primary and not has_fb:
        return None

    def factory(_index, url, model, _headers):
        effort = effort_for_route(url, model, owner)
        return {"kwargs": {"reasoning_effort": effort}} if effort else {}

    return factory

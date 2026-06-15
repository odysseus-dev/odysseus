"""Routes for library context ref preflight checks."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.session_manager import SessionManager
from src.auth_helpers import get_current_user
from src.context_budget import compute_input_token_budget
from src.context_refs import (
    estimate_ref_tokens,
    validate_refs,
    MAX_REFS_PER_MESSAGE,
)
from src.model_context import estimate_tokens, get_context_length
from src.settings import load_settings, is_setting_overridden
from routes.session_routes import _verify_session_owner

logger = logging.getLogger(__name__)

RESERVE_TOKENS = 1024


class PreflightCandidate(BaseModel):
    type: str = Field(..., pattern=r"^(document|research|session)$")
    id: str
    title: str = ""


class PreflightRequest(BaseModel):
    session_id: str
    refs: List[PreflightCandidate] = Field(default_factory=list)
    candidate: Optional[PreflightCandidate] = None


def setup_context_refs_routes(session_manager: SessionManager) -> APIRouter:
    router = APIRouter(prefix="/api/context_refs", tags=["context_refs"])

    @router.post("/preflight")
    async def context_refs_preflight(body: PreflightRequest, request: Request):
        """Check whether adding one or more context refs would exceed the model's
        input token budget for the target session.
        """
        owner = get_current_user(request)
        _verify_session_owner(request, body.session_id, session_manager=session_manager)

        try:
            sess = session_manager.get_session(body.session_id)
        except KeyError:
            raise HTTPException(404, f"Session '{body.session_id}' not found")

        try:
            context_length = get_context_length(sess.endpoint_url, sess.model)
        except Exception as e:
            logger.warning("Could not determine context length for preflight: %s", e)
            context_length = 0

        settings = load_settings()
        configured_budget = settings.get("agent_input_token_budget")
        hard_max = settings.get("agent_input_token_hard_max")
        explicit = is_setting_overridden("agent_input_token_budget")
        budget = compute_input_token_budget(
            configured=configured_budget,
            context_length=context_length,
            explicit=explicit,
            hard_max=hard_max,
        )

        used_tokens = estimate_tokens(sess.get_context_messages())

        # Validate the sticky refs and optional candidate shape up front.
        all_refs: List[Dict[str, Any]] = [r.model_dump() for r in body.refs]
        if body.candidate:
            all_refs.append(body.candidate.model_dump())
        if len(all_refs) > MAX_REFS_PER_MESSAGE:
            return {
                "ok": False,
                "used_tokens": used_tokens,
                "refs_tokens": 0,
                "budget": budget,
                "context_length": context_length,
                "message": f"At most {MAX_REFS_PER_MESSAGE} context refs can be attached at once.",
            }
        try:
            validate_refs(all_refs)
        except HTTPException as e:
            return {
                "ok": False,
                "used_tokens": used_tokens,
                "refs_tokens": 0,
                "budget": budget,
                "context_length": context_length,
                "message": str(e.detail),
            }

        refs_tokens = 0
        for ref in all_refs:
            try:
                refs_tokens += estimate_ref_tokens(ref, owner)
            except HTTPException as e:
                # Surface owner/auth failures clearly; budget math doesn't matter
                # if we can't read the ref.
                return {
                    "ok": False,
                    "used_tokens": used_tokens,
                    "refs_tokens": 0,
                    "budget": budget,
                    "context_length": context_length,
                    "message": str(e.detail),
                }

        total = used_tokens + refs_tokens + RESERVE_TOKENS
        ok = total <= budget
        remaining = max(0, budget - used_tokens - RESERVE_TOKENS)

        if ok:
            message = "Context ref fits within the model's budget."
        else:
            title = "this source"
            if body.candidate:
                title = body.candidate.title or body.candidate.id
            message = (
                f'Adding "{title}" needs ~{refs_tokens} tokens, but only ~{remaining} '
                f"remain for this model ({budget} budget, {used_tokens} already in chat). "
                "Remove a context chip or pick a shorter source."
            )

        return {
            "ok": ok,
            "used_tokens": used_tokens,
            "refs_tokens": refs_tokens,
            "budget": budget,
            "context_length": context_length,
            "message": message,
        }

    return router

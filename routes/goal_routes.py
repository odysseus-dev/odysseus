"""Goal routes for per-session agent goals."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routes.session_routes import _verify_session_owner
from src.auth_helpers import effective_user
from src.agent_goals import (
    GoalConflictError,
    GoalError,
    GoalNotFoundError,
    clear_goal,
    get_goal,
    patch_goal,
    set_goal,
)
from src.goal_runner import start_goal_continuation


class GoalCreateRequest(BaseModel):
    objective: str
    token_budget: Optional[int] = None
    replace: bool = False


class GoalPatchRequest(BaseModel):
    objective: Optional[str] = None
    status: Optional[str] = None
    token_budget: Optional[int] = None


def _owner(request: Request) -> Optional[str]:
    return effective_user(request)


def _goal_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GoalNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, GoalConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, GoalError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "goal operation failed")


def setup_goal_routes() -> APIRouter:
    router = APIRouter(prefix="/api/goals", tags=["goals"])

    @router.get("/{session_id}")
    async def get_session_goal(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        return {"goal": get_goal(session_id, owner=_owner(request))}

    @router.post("/{session_id}")
    async def create_session_goal(
        request: Request,
        session_id: str,
        payload: GoalCreateRequest,
    ) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        try:
            goal = set_goal(
                session_id,
                payload.objective,
                token_budget=payload.token_budget,
                owner=_owner(request),
                replace=payload.replace,
            )
            return {"goal": goal}
        except Exception as exc:
            raise _goal_error(exc) from exc

    @router.patch("/{session_id}")
    async def patch_session_goal(
        request: Request,
        session_id: str,
        payload: GoalPatchRequest,
    ) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        updates: Dict[str, Any] = {}
        if payload.objective is not None:
            updates["objective"] = payload.objective
        if payload.status is not None:
            updates["status"] = payload.status
        fields_set = getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set()))
        if "token_budget" in fields_set:
            updates["token_budget"] = payload.token_budget
        try:
            goal = patch_goal(session_id, owner=_owner(request), **updates)
            return {"goal": goal}
        except Exception as exc:
            raise _goal_error(exc) from exc

    @router.delete("/{session_id}")
    async def clear_session_goal(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        return {"cleared": clear_goal(session_id, owner=_owner(request))}

    @router.post("/{session_id}/continue")
    async def continue_session_goal(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        started, reason, goal = start_goal_continuation(session_id, owner=_owner(request))
        return {"started": started, "eligible": started, "reason": reason, "goal": goal}

    return router

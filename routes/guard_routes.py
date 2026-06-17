"""Guard routes — MEDIUM-risk approval flow endpoints."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.guard_approvals import decide, get_pending_for_owner


class DecideBody(BaseModel):
    decision: str  # "approve" or "deny"


def setup_guard_routes() -> APIRouter:
    router = APIRouter(prefix="/api/guard", tags=["guard"])

    def _owner(request: Request) -> str:
        owner = get_current_user(request)
        if not owner:
            raise HTTPException(status_code=401, detail="Authentication required")
        return owner

    @router.get("/pending")
    def list_pending(request: Request):
        """List pending MEDIUM-risk approval requests for the current user."""
        owner = _owner(request)
        return {"pending": get_pending_for_owner(owner)}

    @router.post("/decide/{token}")
    def decide_approval(token: str, body: DecideBody, request: Request):
        """Approve or deny a pending command execution."""
        owner = _owner(request)
        if body.decision not in ("approve", "deny"):
            raise HTTPException(status_code=400, detail="decision must be 'approve' or 'deny'")
        record = decide(token, owner, body.decision)
        if record is None:
            raise HTTPException(status_code=404, detail="Token not found, expired, or not yours")
        return {"token": token, "decision": body.decision, "command": record["command"]}

    return router

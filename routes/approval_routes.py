"""Approval decision endpoint for manual / accept-edits agent modes.

The agent loop, when a tool needs approval, emits an ``approval_required`` SSE
event carrying an id and awaits a decision. The client POSTs here to resolve it.
"""
import os

from fastapi import APIRouter, HTTPException, Request


def setup_approval_routes() -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["approvals"])

    @router.post("/approval")
    async def submit_approval(request: Request):
        # Require a logged-in user (the auth middleware stamps current_user).
        if os.getenv("AUTH_ENABLED", "true").lower() != "false":
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if auth_mgr and getattr(auth_mgr, "is_configured", False):
                if not getattr(request.state, "current_user", None):
                    raise HTTPException(401, "Login required")

        body = await request.json()
        from src import approvals
        ok = approvals.resolve(
            str(body.get("id", "")),
            approved=bool(body.get("approved")),
            remember=bool(body.get("remember")),
            requester=getattr(request.state, "current_user", None),
        )
        if not ok:
            raise HTTPException(404, "No pending approval with that id (it may have timed out).")
        return {"ok": True}

    return router

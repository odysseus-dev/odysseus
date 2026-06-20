"""System self-update routes — admin-only.

Lets an admin check for and apply updates from the upstream project repo
(community/owner) from inside the app, without depending on the scheduled
launchd job (which only fires when the Mac is on at 05:00). The heavy lifting
and the conflict-safe merge live in ``src/self_update.py``; these handlers are
thin wrappers gated by ``require_admin``.
"""

from fastapi import APIRouter, Depends, Request

from core.middleware import require_admin
from src import self_update


def setup_system_update_routes() -> APIRouter:
    router = APIRouter(prefix="/api/system/update", tags=["system-update"])

    @router.get("/available")
    def update_available(request: Request, _admin: None = Depends(require_admin)):
        """Whether self-update is possible here (git checkout + upstream remote)."""
        return {"supported": self_update.is_supported()}

    @router.get("/check")
    def update_check(request: Request, _admin: None = Depends(require_admin)):
        """Fetch upstream and report what an update would bring. Read-only."""
        return self_update.check_updates()

    @router.post("/apply")
    def update_apply(request: Request, _admin: None = Depends(require_admin)):
        """Conflict-safe merge of upstream. Aborts and changes nothing on conflict."""
        return self_update.apply_update()

    return router

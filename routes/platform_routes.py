"""Business-platform HTTP surface (Big Boss message plane). Spec §3, §7.

Registry endpoints are admin-only. Approval decisions additionally require
the caller to be a manager of the intent's origin company (enforced in the
approval service). Envelope ingest authenticates by SIGNATURE, not by web
session: the hub verifies the sending company's Ed25519 signature.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from services.business_platform.envelope import Envelope
from services.business_platform import hub, registry, approval

logger = logging.getLogger(__name__)


class CompanyIn(BaseModel):
    id: str
    vertical_type: str
    display_name: str = ""
    manager_principal_id: Optional[str] = None
    parent_id: Optional[str] = None
    surface_policy: str = "web_first"


class IngestIn(BaseModel):
    envelope: Envelope
    signature: str


def setup_platform_routes() -> APIRouter:
    router = APIRouter(prefix="/api/platform", tags=["platform"])

    # --- registry (admin-only control over tenants) --------------------------
    @router.post("/companies")
    def create_company(request: Request, body: CompanyIn):
        require_admin(request)
        try:
            return registry.create_company(
                body.id, body.vertical_type, body.display_name,
                manager_principal_id=body.manager_principal_id,
                parent_id=body.parent_id, surface_policy=body.surface_policy)
        except registry.RegistryError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/companies/{company_id}")
    def get_company(request: Request, company_id: str):
        require_admin(request)
        c = registry.get_company(company_id)
        if not c:
            raise HTTPException(status_code=404, detail="company not found")
        return c

    # --- hub message plane ----------------------------------------------------
    @router.post("/envelopes")
    def ingest_envelope(body: IngestIn):
        try:
            return hub.ingest(body.envelope, body.signature)
        except hub.HubError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/inbox/{company_id}")
    def poll_inbox(request: Request, company_id: str):
        require_admin(request)   # slice-1: company runtimes poll via admin token
        return hub.poll_inbox(company_id)

    # --- manager approval queue ----------------------------------------------
    @router.get("/approvals")
    def list_approvals(request: Request):
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="not authenticated")
        return approval.pending_for_manager(user)

    @router.post("/approvals/{intent_id}/approve")
    def approve_intent(request: Request, intent_id: str):
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="not authenticated")
        try:
            return approval.approve(intent_id, user)
        except approval.ApprovalError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @router.post("/approvals/{intent_id}/deny")
    def deny_intent(request: Request, intent_id: str):
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="not authenticated")
        try:
            return approval.deny(intent_id, user)
        except approval.ApprovalError as e:
            raise HTTPException(status_code=403, detail=str(e))

    return router

# routes/compare_routes.py
"""Model A/B comparison routes."""
import json
import uuid
import random
from datetime import datetime
from fastapi import APIRouter, Form, HTTPException, Request
from typing import List
from pydantic import BaseModel
import logging

from core.database import Comparison, SessionLocal
from core.session_manager import SessionManager
from src.auth_helpers import get_current_user
from routes.session_routes import _reject_raw_endpoint_url_for_non_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compare", tags=["compare"])


def _owned_endpoint_by_url(db, base_url, owner):
    """ModelEndpoint whose base_url == `base_url` and is VISIBLE to `owner`
    (their own rows + legacy null-owner "shared" rows); None otherwise.

    Owner-scoped on purpose. ModelEndpoint is per-user (core/database.py: non-null
    owner = private, "the model picker only shows the endpoint to that user") and
    holds a decrypted `api_key`. start_comparison copies the matched row's api_key
    into the caller-owned [CMP] session's headers, which then drives that session's
    /api/chat_stream calls — so an UNSCOPED base_url match would let a user mint a
    comparison bound to ANOTHER user's private endpoint and spend that owner's
    api_key / reach whatever base_url they configured. Mirrors
    session_routes._owned_endpoint. A null/empty owner is a no-op (single-user /
    legacy mode).
    """
    from core.database import ModelEndpoint
    from src.auth_helpers import owner_filter
    q = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base_url)
    return owner_filter(q, ModelEndpoint, owner).first()


def _owned_endpoint_by_id(db, endpoint_id, owner):
    """ModelEndpoint whose id == `endpoint_id` and is VISIBLE to `owner` (their
    own rows + legacy null-owner "shared" rows); None otherwise.

    Preferred over _owned_endpoint_by_url for credential resolution: two visible
    endpoints can share the same base_url but hold DIFFERENT api_keys (e.g. two
    accounts on the same provider). A base_url-only match returns whichever row
    sorts first, so it can copy the WRONG owner-scoped key into the [CMP] session.
    An id pins the exact registered endpoint, so /api/compare/start prefers it and
    only falls back to URL matching for legacy / admin raw-URL callers. Owner
    scoping is identical to _owned_endpoint_by_url (a null/empty owner is a no-op).
    """
    from core.database import ModelEndpoint
    from src.auth_helpers import owner_filter
    q = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id)
    return owner_filter(q, ModelEndpoint, owner).first()


class RecordVoteRequest(BaseModel):
    prompt: str
    models: List[str]
    winner: str           # model name or "tie"
    is_blind: bool = True


def setup_compare_routes(session_manager: SessionManager):
    """Setup comparison routes."""

    @router.post("/start")
    def start_comparison(
        request: Request,
        prompt: str = Form(...),
        model_a: str = Form(...),
        model_b: str = Form(...),
        endpoint_a: str = Form(""),
        endpoint_b: str = Form(""),
        endpoint_a_id: str = Form(""),
        endpoint_b_id: str = Form(""),
        is_blind: str = Form("true"),
    ):
        """Create two ephemeral sessions and a comparison record.

        Returns the comparison ID and the two session IDs so the client
        can fire two independent SSE streams to /api/chat_stream.
        """
        user = getattr(request.state, 'current_user', None)
        comp_id = str(uuid.uuid4())
        sid_a = str(uuid.uuid4())
        sid_b = str(uuid.uuid4())

        # Blind mapping: randomly assign left/right
        blind = str(is_blind).lower() == "true"
        if blind:
            mapping = {"left": "a", "right": "b"}
            if random.random() > 0.5:
                mapping = {"left": "b", "right": "a"}
        else:
            mapping = {"left": "a", "right": "b"}

        # Map session IDs to left/right based on blind mapping
        session_left = sid_a if mapping["left"] == "a" else sid_b
        session_right = sid_a if mapping["right"] == "a" else sid_b

        # In blind mode, name the helper sessions by their neutral slot
        # ("Model A" / "Model B") instead of the real model. Otherwise the
        # session name leaks the model in the sidebar and GET /api/sessions,
        # de-anonymizing the comparison before the user votes (issue #1285).
        slot_name = {session_left: "Model A", session_right: "Model B"}

        # SECURITY: resolve and validate BOTH endpoints before creating any
        # session. Compare copies a registered endpoint's Authorization header
        # into the [CMP] session, so validating one endpoint while creating its
        # session, then rejecting the other, would leave a partial compare
        # session behind with that header attached. Doing all the owner-scope
        # resolution + raw-URL rejection up front means a 403 on either endpoint
        # aborts the whole request with nothing created and no header copied.
        from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
        resolved = []
        db = SessionLocal()
        try:
            for sid, model, endpoint, endpoint_id in [
                (sid_a, model_a, endpoint_a, endpoint_a_id),
                (sid_b, model_b, endpoint_b, endpoint_b_id),
            ]:
                # Prefer an explicit endpoint id: it pins the EXACT registered
                # endpoint (and its api_key), even when two endpoints visible to
                # the caller share a base_url with different keys — a URL-only
                # match would copy whichever row sorts first, i.e. possibly the
                # wrong key. Fall back to URL resolution only for legacy / admin
                # raw-URL callers that don't send an id.
                eid = endpoint_id.strip() if isinstance(endpoint_id, str) else ""
                if eid:
                    ep = _owned_endpoint_by_id(db, eid, user)
                    if ep is None:
                        # An id the caller can't see (wrong owner / deleted) must
                        # NOT silently fall back to a same-URL row with a different
                        # key — that's exactly the mix-up ids exist to prevent.
                        raise HTTPException(404, "Model endpoint not found")
                    # The id already resolved the endpoint; ignore any raw URL the
                    # caller also sent and dial the stored config instead.
                    endpoint = ep.base_url
                elif not endpoint:
                    raise HTTPException(
                        422, "endpoint_a/endpoint_b or endpoint_a_id/endpoint_b_id is required"
                    )
                else:
                    # Resolve the supplied URL to a ModelEndpoint the caller owns
                    # (their own rows + legacy null-owner shared rows), scoped so a
                    # comparison can't borrow another user's private endpoint key.
                    base = normalize_base(endpoint)
                    ep = _owned_endpoint_by_url(db, base, user)
                # Reject *unregistered* raw URLs for signed-in non-admins; a
                # matched registered endpoint supplies an id so the caller can
                # still compare endpoints they own. Blanket-rejecting here (the
                # earlier `endpoint_id=None` call) locked non-admins out of
                # compare entirely, since compare resolves endpoints by URL with
                # no endpoint_id. Mirrors the gallery inpaint/harmonize checks.
                # Raised here (phase 1), before any session exists.
                _reject_raw_endpoint_url_for_non_admin(
                    request, user, str(ep.id) if ep is not None else None, endpoint
                )
                # Bind the [CMP] session to the RESOLVED endpoint, not the raw
                # caller-supplied string. When the URL matches a registered
                # endpoint visible to the caller, use that row's own normalized
                # base URL (the same value owner scoping + endpoint validation
                # already vetted) so the session dials exactly where the stored
                # config points. The raw `endpoint` only survives for callers
                # allowed to pass one — admins / single-user mode, where
                # `_reject_raw_endpoint_url_for_non_admin` is a no-op and `ep`
                # is None. Mirrors the registered-endpoint path in session_routes.
                session_endpoint_url = (
                    build_chat_url(normalize_base(ep.base_url)) if ep is not None else endpoint
                )
                # Headers come only from a matched endpoint's key; None when
                # `ep` is None (raw admin URL or no match), so a comparison can
                # never inherit another user's key/headers.
                headers = build_headers(ep.api_key, ep.base_url) if (ep and ep.api_key) else None
                resolved.append((sid, model, session_endpoint_url, headers))
        finally:
            db.close()

        # Both endpoints validated — only now create the ephemeral [CMP]
        # sessions and copy any resolved headers.
        for sid, model, session_endpoint_url, headers in resolved:
            name = f"[CMP] {slot_name[sid]}" if blind else f"[CMP] {model.split('/')[-1]}"
            session_manager.create_session(
                session_id=sid,
                name=name,
                endpoint_url=session_endpoint_url,
                model=model,
                rag=False,
                owner=user,
            )
            if headers:
                s = session_manager.sessions.get(sid)
                if s:
                    s.headers = headers

        # Store comparison record
        db = SessionLocal()
        try:
            comp = Comparison(
                id=comp_id,
                prompt=prompt,
                model_a=model_a,
                model_b=model_b,
                # Record the URL the session actually dials. For URL callers this
                # is their raw input; for id-only callers (empty endpoint_a/_b)
                # fall back to the resolved endpoint URL so the column stays
                # meaningful and non-null. resolved is in [a, b] order.
                endpoint_a=endpoint_a or resolved[0][2],
                endpoint_b=endpoint_b or resolved[1][2],
                is_blind=blind,
                blind_mapping=json.dumps(mapping),
                owner=user,
            )
            db.add(comp)
            db.commit()
        finally:
            db.close()

        # In blind mode, withhold the model identities AND the left/right
        # mapping from the response. The client already knows model_a/model_b
        # (it sent them), so returning either would defeat blind mode. They are
        # revealed by POST /api/compare/{id}/vote once the user has voted (#1285).
        return {
            "id": comp_id,
            "session_left": session_left,
            "session_right": session_right,
            "model_left": None if blind else (model_a if mapping["left"] == "a" else model_b),
            "model_right": None if blind else (model_a if mapping["right"] == "a" else model_b),
            "is_blind": blind,
            "mapping": None if blind else mapping,
        }

    @router.post("/{comp_id}/vote")
    def vote_comparison(
        request: Request,
        comp_id: str,
        winner: str = Form(...),  # "left", "right", or "tie"
    ):
        """Record the user's vote and reveal model names if blind."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            comp = db.query(Comparison).filter(Comparison.id == comp_id).first()
            if not comp:
                raise HTTPException(404, "Comparison not found")
            # SECURITY: strict ownership — null-owner Comparisons were
            # accessible to every user.
            if user and comp.owner != user:
                raise HTTPException(404, "Comparison not found")
            if comp.winner:
                raise HTTPException(400, "Already voted")

            mapping = json.loads(comp.blind_mapping) if comp.blind_mapping else {"left": "a", "right": "b"}

            if winner == "tie":
                comp.winner = "tie"
            elif winner == "left":
                comp.winner = mapping["left"]
            elif winner == "right":
                comp.winner = mapping["right"]
            else:
                raise HTTPException(400, "winner must be 'left', 'right', or 'tie'")

            comp.voted_at = datetime.utcnow()
            db.commit()

            return {
                "winner": comp.winner,
                "model_a": comp.model_a,
                "model_b": comp.model_b,
                "revealed": {
                    "left": comp.model_a if mapping["left"] == "a" else comp.model_b,
                    "right": comp.model_a if mapping["right"] == "a" else comp.model_b,
                },
            }
        finally:
            db.close()

    @router.post("/record")
    def record_comparison(request: Request, body: RecordVoteRequest):
        """Lightweight endpoint to record a comparison vote from the frontend."""
        user = get_current_user(request)
        comp_id = str(uuid.uuid4())

        model_a = body.models[0] if len(body.models) > 0 else ""
        model_b = body.models[1] if len(body.models) > 1 else ""

        # For N>2 models, store the full list as JSON in blind_mapping
        if len(body.models) > 2:
            blind_mapping = json.dumps({"models": body.models})
        else:
            blind_mapping = None

        db = SessionLocal()
        try:
            comp = Comparison(
                id=comp_id,
                prompt=body.prompt[:500],
                model_a=model_a,
                model_b=model_b,
                endpoint_a="",
                endpoint_b="",
                winner=body.winner,
                is_blind=body.is_blind,
                blind_mapping=blind_mapping,
                voted_at=datetime.utcnow(),
                owner=user,
            )
            db.add(comp)
            db.commit()
        finally:
            db.close()

        return {"status": "ok", "id": comp_id}

    @router.get("/history")
    def list_comparisons(request: Request):
        """List past comparisons."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(Comparison)
            if user:
                q = q.filter(Comparison.owner == user)
            comps = q.order_by(Comparison.created_at.desc()).limit(50).all()
            return [
                {
                    "id": c.id,
                    "prompt": c.prompt[:100],
                    "model_a": c.model_a,
                    "model_b": c.model_b,
                    "winner": c.winner,
                    "is_blind": c.is_blind,
                    "voted_at": c.voted_at.isoformat() if c.voted_at else None,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in comps
            ]
        finally:
            db.close()

    @router.delete("/{comp_id}")
    def delete_comparison(request: Request, comp_id: str):
        """Delete a comparison and its ephemeral sessions."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            comp = db.query(Comparison).filter(Comparison.id == comp_id).first()
            if not comp:
                raise HTTPException(404, "Comparison not found")
            # SECURITY: strict ownership — null-owner Comparisons were
            # accessible to every user.
            if user and comp.owner != user:
                raise HTTPException(404, "Comparison not found")
            db.delete(comp)
            db.commit()
            return {"status": "deleted"}
        finally:
            db.close()

    return router

# routes/compare_routes.py
"""Model A/B comparison routes."""
import json
import uuid
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Form, HTTPException, Request
from typing import List
from pydantic import BaseModel
import logging

from core.database import Comparison, SessionLocal
from core.session_manager import SessionManager
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compare", tags=["compare"])


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
        endpoint_a: str = Form(...),
        endpoint_b: str = Form(...),
        is_blind: str = Form("true"),
    ):
        """Create two ephemeral sessions and a comparison record.

        Returns the comparison ID and the two session IDs so the client
        can fire two independent SSE streams to /api/chat_stream.
        """
        comp_id = str(uuid.uuid4())
        sid_a = str(uuid.uuid4())
        sid_b = str(uuid.uuid4())

        # Create ephemeral sessions (prefixed [CMP])
        for sid, model, endpoint in [(sid_a, model_a, endpoint_a), (sid_b, model_b, endpoint_b)]:
            user = getattr(request.state, 'current_user', None)
            session_manager.create_session(
                session_id=sid,
                name=f"[CMP] {model.split('/')[-1]}",
                endpoint_url=endpoint,
                model=model,
                rag=False,
                owner=user,
            )
            # Copy API key from endpoint config
            db = SessionLocal()
            try:
                from core.database import ModelEndpoint
                from src.endpoint_resolver import build_headers, normalize_base
                # Find matching endpoint by URL
                base = normalize_base(endpoint)
                ep = db.query(ModelEndpoint).filter(
                    ModelEndpoint.base_url == base
                ).first()
                if ep and ep.api_key:
                    s = session_manager.sessions.get(sid)
                    if s:
                        s.headers = build_headers(ep.api_key, ep.base_url)
            finally:
                db.close()

        # Blind mapping: randomly assign left/right
        blind = str(is_blind).lower() == "true"
        if blind:
            mapping = {"left": "a", "right": "b"}
            if random.random() > 0.5:
                mapping = {"left": "b", "right": "a"}
        else:
            mapping = {"left": "a", "right": "b"}

        # Store comparison record
        db = SessionLocal()
        try:
            comp = Comparison(
                id=comp_id,
                prompt=prompt,
                model_a=model_a,
                model_b=model_b,
                endpoint_a=endpoint_a,
                endpoint_b=endpoint_b,
                is_blind=blind,
                blind_mapping=json.dumps(mapping),
                owner=user,
            )
            db.add(comp)
            db.commit()
        finally:
            db.close()

        # Map session IDs to left/right based on blind mapping
        session_left = sid_a if mapping["left"] == "a" else sid_b
        session_right = sid_a if mapping["right"] == "a" else sid_b

        return {
            "id": comp_id,
            "session_left": session_left,
            "session_right": session_right,
            "model_left": model_a if mapping["left"] == "a" else model_b,
            "model_right": model_a if mapping["right"] == "a" else model_b,
            "is_blind": blind,
            "mapping": mapping,
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

            comp.voted_at = datetime.now(timezone.utc)
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
                voted_at=datetime.now(timezone.utc),
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

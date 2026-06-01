"""Routes for Juniperus / Gnexus Operations Console - Local Ollama model readiness.

Serves the /gnexus/ollama-models page and a resilient JSON state endpoint.
Local-first; no cloud calls. The page is usable even if the API is blocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from src.gnexus_governance import ollama_readiness as olr


def _endpoint_registered() -> Optional[bool]:
    """Return True/False if the Local Ollama endpoint exists in the model DB,
    or None if the DB cannot be inspected."""
    try:
        from core.database import SessionLocal, ModelEndpoint  # type: ignore
    except Exception:
        return None
    db = None
    try:
        db = SessionLocal()
        row = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == olr.ENDPOINT_BASE_URL)
            .first()
        )
        if row is None:
            # also accept normalized variants
            row = (
                db.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url.like("%11434%"))
                .first()
            )
        return bool(row)
    except Exception:
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _state() -> Dict[str, Any]:
    # Prefer a fresh live build; fall back to cached registry if detection fails.
    reg = olr.build_registry()
    cached = olr.load_registry()
    if not reg["ollama"]["running"] and cached:
        # Surface cached model list while clearly marking offline.
        cached.setdefault("ollama", {})["running"] = False
        cached["ollama"]["source"] = "cached"
        reg = cached
    reg["endpoint"]["registered_in_picker"] = _endpoint_registered()
    reg["smoke"] = olr.load_smoke()
    return reg


def setup_gnexus_ollama_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-ollama"])

    @router.get("/gnexus/ollama-models", include_in_schema=False)
    async def ollama_models_page():
        page = Path("static") / "gnexus" / "ollama-models.html"
        if not page.exists():
            raise HTTPException(404, "ollama-models.html not found")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @router.get("/api/gnexus/ollama/state")
    async def ollama_state(request: Request):
        return JSONResponse(_state())

    @router.post("/api/gnexus/ollama/smoke")
    async def ollama_smoke(request: Request, body: Optional[dict] = None):
        model = None
        if isinstance(body, dict):
            model = body.get("model")
        return JSONResponse(olr.run_smoke_test(model=model))

    return router

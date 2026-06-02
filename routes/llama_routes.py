"""Local llama.cpp model hub + serving routes.

Admin-only HTTP API over services/llama.LlamaManager. Provisions a prebuilt
llama-server, downloads GGUF models, serves them with GPU offload, and
auto-registers a running server as a ModelEndpoint so it shows up in the chat
model picker — a real local-LLM path that works out-of-the-box on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.llama import get_llama_manager
from services.llama.manager import LLAMA_RELEASE_TAG

logger = logging.getLogger(__name__)


def _require_admin(request: Request):
    """Reject non-admin callers (mirrors shell_routes._require_admin)."""
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        return  # no auth subsystem -> dev mode
    user = getattr(request.state, "current_user", None)
    if user == "internal-tool":
        return
    if not user:
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")


class DownloadReq(BaseModel):
    repo_id: str
    filename: str | None = None
    hf_token: str | None = None


class ServeReq(BaseModel):
    # Field is `auto_register` but keeps its wire name "register" via alias.
    # Naming the Python field `register` shadowed BaseModel.register and emitted
    # a Pydantic field-shadow UserWarning at import time.
    model_config = ConfigDict(populate_by_name=True)
    gguf_path: str
    model_id: str | None = None
    n_gpu_layers: int | None = None
    ctx_size: int = 4096
    auto_register: bool = Field(True, alias="register")  # auto-register as a chat ModelEndpoint when ready


class StopReq(BaseModel):
    model_id: str


def setup_llama_routes() -> APIRouter:
    router = APIRouter(prefix="/api/llama", tags=["llama"])
    mgr = get_llama_manager()

    @router.get("/status")
    async def status(request: Request):
        _require_admin(request)
        srv = mgr.server_path()
        return {
            "release_tag": LLAMA_RELEASE_TAG,
            "backend": mgr.backend_kind(),
            "binary_provisioned": srv is not None,
            "binary_path": str(srv) if srv else None,
            "servers": mgr.list_servers(),
        }

    @router.post("/provision")
    async def provision(request: Request):
        _require_admin(request)
        try:
            srv = await asyncio.to_thread(mgr.ensure_binary)
            return {"ok": True, "binary_path": str(srv), "backend": mgr.backend_kind()}
        except Exception as e:
            logger.exception("llama provision failed")
            return {"ok": False, "error": str(e)}

    @router.get("/models/local")
    async def local_models(request: Request):
        _require_admin(request)
        return {"models": mgr.list_local_models()}

    @router.post("/download")
    async def download(request: Request, req: DownloadReq):
        _require_admin(request)
        try:
            path = await asyncio.to_thread(
                mgr.download_gguf, req.repo_id, req.filename, req.hf_token
            )
            return {"ok": True, "path": path}
        except Exception as e:
            logger.exception("llama download failed")
            return {"ok": False, "error": str(e)}

    @router.post("/serve")
    async def serve(request: Request, req: ServeReq):
        _require_admin(request)
        try:
            handle = await asyncio.to_thread(
                mgr.serve, req.gguf_path, req.model_id, req.n_gpu_layers, req.ctx_size
            )
            ready = await asyncio.to_thread(mgr.wait_until_ready, handle, 120)
            result = handle.to_dict()
            result["ready"] = ready
            if ready and req.auto_register:
                ep_id = _register_endpoint(handle)
                result["endpoint_id"] = ep_id
            return {"ok": ready, "server": result,
                    "error": None if ready else "Server did not become ready in time"}
        except Exception as e:
            logger.exception("llama serve failed")
            return {"ok": False, "error": str(e)}

    @router.post("/stop")
    async def stop(request: Request, req: StopReq):
        _require_admin(request)
        ok = mgr.stop(req.model_id)
        return {"ok": ok}

    @router.get("/health/{model_id}")
    async def health(request: Request, model_id: str):
        _require_admin(request)
        return mgr.health(model_id)

    def _register_endpoint(handle) -> str | None:
        """Auto-register the running llama-server as a chat ModelEndpoint."""
        try:
            from core.database import SessionLocal, ModelEndpoint
            base_url = handle.base_url
            display = f"{handle.model_id} (local llama.cpp)"
            db = SessionLocal()
            try:
                existing = db.query(ModelEndpoint).filter(
                    ModelEndpoint.base_url == base_url).first()
                if existing:
                    existing.is_enabled = True
                    existing.name = display
                    existing.model_type = "llm"
                    db.commit()
                    return existing.id
                ep_id = f"llama-{uuid.uuid4().hex[:8]}"
                ep = ModelEndpoint(
                    id=ep_id, name=display, base_url=base_url,
                    api_key=None, is_enabled=True, model_type="llm",
                )
                db.add(ep)
                db.commit()
                logger.info("Auto-registered llama endpoint: %s @ %s", display, base_url)
                return ep_id
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to auto-register llama endpoint")
            return None

    return router

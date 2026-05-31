import os
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from core.constants import BASE_DIR, APP_VERSION
from src.app_helpers import abs_join


class RevalidatingStatic(StaticFiles):
    """Serve static assets normally, but force the browser to REVALIDATE
    source files (.js/.css/.html) on every load instead of serving a stale
    copy from disk cache."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def _serve_html_with_nonce(request: Request, file_path: str) -> HTMLResponse:
    with open(file_path, "r") as f:
        html = f.read()
    nonce = getattr(request.state, "csp_nonce", "")
    html = html.replace("{{CSP_NONCE}}", nonce)
    return HTMLResponse(html)


def setup_spa_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/generated-image/{filename}")
    async def serve_generated_image(filename: str, request: Request):
        from pathlib import Path
        import re
        if not re.match(r'^[a-f0-9]{8,64}\.(png|jpg|jpeg|webp|gif|mp4|mov|webm|mkv|m4v)$', filename):
            raise HTTPException(status_code=400, detail="Invalid filename")
        img_path = Path("data/generated_images") / filename
        if not img_path.exists():
            raise HTTPException(status_code=404, detail="Image not found")
        try:
            from src.auth_helpers import get_current_user
            from core.database import SessionLocal as _SL, GalleryImage as _GI
            _user = get_current_user(request)
            if _user:
                _db = _SL()
                try:
                    _row = _db.query(_GI).filter(_GI.filename == filename).first()
                    if _row is not None and _row.owner and _row.owner != _user:
                        raise HTTPException(status_code=404, detail="Image not found")
                finally:
                    _db.close()
        except HTTPException:
            raise
        except Exception:
            pass
        ext = filename.rsplit('.', 1)[-1].lower()
        mime = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif",
            "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
            "mkv": "video/x-matroska", "m4v": "video/mp4",
        }.get(ext, "application/octet-stream")
        return FileResponse(
            str(img_path),
            media_type=mime,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @router.get("/")
    async def serve_index(request: Request):
        static_path = abs_join(BASE_DIR, "static/index.html")
        if os.path.exists(static_path):
            return _serve_html_with_nonce(request, static_path)
        root_path = abs_join(BASE_DIR, "index.html")
        if os.path.exists(root_path):
            return _serve_html_with_nonce(request, root_path)
        raise HTTPException(404, "index.html not found")

    @router.get("/notes")
    @router.get("/calendar")
    @router.get("/cookbook")
    @router.get("/email")
    @router.get("/memory")
    @router.get("/gallery")
    @router.get("/tasks")
    @router.get("/library")
    async def serve_spa_page(request: Request):
        return await serve_index(request)

    @router.get("/backgrounds")
    async def serve_backgrounds(request: Request):
        return _serve_html_with_nonce(request, abs_join(BASE_DIR, "static/backgrounds.html"))

    @router.get("/login")
    async def serve_login(request: Request):
        return _serve_html_with_nonce(request, abs_join(BASE_DIR, "static/login.html"))

    @router.get("/api/version")
    async def get_version():
        return {"version": APP_VERSION}

    @router.get("/api/health")
    async def health_check() -> Dict[str, str]:
        return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

    return router

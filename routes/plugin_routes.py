"""Plugin system API — discover, install, list, and uninstall plugins."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse

from src.plugin_manager import PluginManager
from src.auth_helpers import get_current_user
from core.middleware import require_admin


def setup_plugin_routes():
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])
    pm = PluginManager()

    @router.get("")
    async def list_plugins(request: Request):
        require_admin(request)
        return {"installed": pm.list_installed()}

    @router.post("/discover")
    async def discover_plugins(request: Request, body: dict):
        require_admin(request)
        url = (body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url required")
        results = pm.discover(url)
        return {"plugins": results}

    @router.post("/install")
    async def install_plugins(request: Request, body: dict):
        require_admin(request)
        url = (body.get("url") or "").strip()
        ids = body.get("ids", [])
        if not url or not ids:
            raise HTTPException(400, "url and ids required")
        if not isinstance(ids, list):
            raise HTTPException(400, "ids must be a list")
        result = pm.install(url, ids)
        return result

    @router.delete("/{plugin_name}")
    async def uninstall_plugin(request: Request, plugin_name: str):
        require_admin(request)
        if pm.uninstall(plugin_name):
            return {"ok": True}
        raise HTTPException(404, "Plugin not found")

    @router.post("/{plugin_name}/toggle")
    async def toggle_plugin(request: Request, plugin_name: str, body: dict):
        require_admin(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(400, "enabled must be a boolean")
        pm.set_enabled(plugin_name, enabled)
        return {"ok": True, "enabled": enabled}

    @router.post("/updates")
    async def check_updates(request: Request):
        require_admin(request)
        updates = pm.check_updates()
        return {"updates": updates}

    @router.get("/static/{plugin_name}/{filepath:path}")
    async def plugin_static(request: Request, plugin_name: str, filepath: str):
        target = pm.serve_path(plugin_name, filepath)
        if not target:
            raise HTTPException(404, "Not found")
        return FileResponse(target)

    return router

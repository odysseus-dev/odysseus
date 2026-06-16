"""Plugin system API — list, toggle, uninstall, and serve static files.

Remote discover/install are intentionally left out of this PR;
they will be revisited separately once the contract is settled.
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse

from src.plugin_manager import PluginManager
from core.middleware import require_admin


def setup_plugin_routes():
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])
    pm = PluginManager()

    @router.get("")
    async def list_plugins(request: Request):
        require_admin(request)
        return {"installed": pm.list_installed()}

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

    @router.get("/static/{plugin_name}/{filepath:path}")
    async def plugin_static(request: Request, plugin_name: str, filepath: str):
        target = pm.serve_path(plugin_name, filepath)
        if not target:
            raise HTTPException(404, "Not found")
        return FileResponse(target)

    return router

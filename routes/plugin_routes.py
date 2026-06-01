"""Admin API for the plugin system — list / enable / disable / reload.

Backs the Settings → Plugins panel. All endpoints are admin-only.
"""
from fastapi import APIRouter, Request, HTTPException

from core.middleware import require_admin


def setup_plugin_routes() -> APIRouter:
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])

    def _mgr():
        from src.plugin_system import get_manager
        m = get_manager()
        if m is None:
            raise HTTPException(503, "Plugin system not initialized")
        return m

    @router.get("")
    async def list_plugins(request: Request):
        require_admin(request)
        return {"plugins": _mgr().list()}

    @router.post("/{plugin_id}/enable")
    async def enable_plugin(plugin_id: str, request: Request):
        require_admin(request)
        try:
            return _mgr().enable(plugin_id)
        except KeyError:
            raise HTTPException(404, "Plugin not found")

    @router.post("/{plugin_id}/disable")
    async def disable_plugin(plugin_id: str, request: Request):
        require_admin(request)
        try:
            return _mgr().disable(plugin_id)
        except KeyError:
            raise HTTPException(404, "Plugin not found")

    @router.post("/{plugin_id}/reload")
    async def reload_plugin(plugin_id: str, request: Request):
        require_admin(request)
        try:
            return _mgr().reload(plugin_id)
        except KeyError:
            raise HTTPException(404, "Plugin not found")

    @router.post("/rescan")
    async def rescan(request: Request):
        """Re-scan the plugins directory (pick up newly dropped-in plugins)."""
        require_admin(request)
        _mgr().load_enabled()
        return {"plugins": _mgr().list()}

    # ---- Registry / depot (browse + install from a curated index) ----------
    @router.get("/registry")
    async def registry(request: Request):
        """Available plugins aggregated across all registries, with install state.
        Returns {"plugins": [...], "sources": [{url, ok, count/error}]}."""
        require_admin(request)
        from src import plugin_registry as reg
        return reg.available()

    @router.get("/registries")
    async def registries(request: Request):
        """The configured registry source URLs (base + user-added)."""
        require_admin(request)
        from src import plugin_registry as reg
        return {"registries": reg.get_registries(), "custom": reg._load_custom()}

    @router.post("/registries")
    async def add_registry(request: Request):
        """Add a custom registry source URL."""
        require_admin(request)
        from src import plugin_registry as reg
        body = await request.json()
        try:
            return {"registries": reg.add_registry((body.get("url") or "").strip())}
        except Exception as e:
            raise HTTPException(400, str(e))

    @router.delete("/registries")
    async def del_registry(request: Request):
        """Remove a custom registry source URL."""
        require_admin(request)
        from src import plugin_registry as reg
        body = await request.json()
        return {"registries": reg.remove_registry((body.get("url") or "").strip())}

    @router.post("/install")
    async def install(request: Request):
        """Install a plugin by registry id ({"id": ...}) or a direct zip
        ({"url": ..., "id": ..., "sha256": ...})."""
        require_admin(request)
        from src import plugin_registry as reg
        body = await request.json()
        try:
            if body.get("url"):
                return reg.install(url=body["url"], plugin_id=body.get("id") or body.get("plugin_id"),
                                   sha256=body.get("sha256"))
            entry = reg.find_entry(body.get("id"))
            if not entry:
                raise HTTPException(404, "plugin id not in any registry")
            return reg.install(entry=entry)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Install failed: {e}")

    @router.post("/{plugin_id}/uninstall")
    async def uninstall(plugin_id: str, request: Request):
        """Disable + delete a plugin's folder."""
        require_admin(request)
        from src import plugin_registry as reg
        try:
            return reg.uninstall(plugin_id)
        except KeyError:
            raise HTTPException(404, "Plugin not found")
        except Exception as e:
            raise HTTPException(400, f"Uninstall failed: {e}")

    return router

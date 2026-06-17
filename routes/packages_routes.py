"""Package manager API routes — install, list, toggle, remove packages."""
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user


class ToggleBody(BaseModel):
    enable: bool


def setup_packages_routes(package_manager):
    router = APIRouter(prefix="/api/packages", tags=["packages"])

    def _require_admin(request: Request) -> str:
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Package management requires admin access")
        return owner

    @router.get("")
    def list_packages(request: Request):
        """List all installed packages."""
        owner = get_current_user(request)
        packages = package_manager.list_packages(owner=owner if not owner_is_admin_or_single_user(owner) else None)
        return {"packages": packages, "count": len(packages)}

    @router.get("/hooks")
    def get_frontend_hooks(request: Request):
        """Get all active frontend hook URLs for the plugin widget system."""
        get_current_user(request)
        return package_manager.get_all_frontend_hooks()

    @router.get("/{pkg_id}")
    def get_package(pkg_id: str, request: Request):
        """Get details for a specific package."""
        get_current_user(request)
        pkg = package_manager.get_package(pkg_id)
        if not pkg:
            raise HTTPException(status_code=404, detail=f"Package '{pkg_id}' not found")
        return pkg

    @router.post("/install")
    async def install_package(request: Request, file: UploadFile = File(...)):
        """
        Upload and install a .zip package.
        Security scan is performed before installation.
        MEDIUM-risk packages are installed but flagged — the caller decides whether to proceed.
        HIGH-risk packages are rejected.
        """
        owner = _require_admin(request)

        if not file.filename or not file.filename.endswith(".zip"):
            raise HTTPException(status_code=400, detail="Only .zip package files are accepted")

        # Save upload to temp file
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / file.filename
        try:
            content = await file.read()
            if len(content) > 50 * 1024 * 1024:  # 50 MB limit
                raise HTTPException(status_code=413, detail="Package too large (max 50 MB)")

            tmp_path.write_bytes(content)

            result = package_manager.install_package(str(tmp_path), owner=owner)
            return JSONResponse(status_code=201, content=result)

        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Installation failed: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @router.patch("/{pkg_id}/toggle")
    def toggle_package(pkg_id: str, body: ToggleBody, request: Request):
        """Enable or disable a package at runtime."""
        _require_admin(request)
        ok = package_manager.toggle_plugin(pkg_id, body.enable)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Package '{pkg_id}' not found")
        return {"success": True, "pkg_id": pkg_id, "enabled": body.enable}

    @router.post("/{pkg_id}/load")
    def load_package(pkg_id: str, request: Request):
        """Manually load (inject) a package's backend into the runtime."""
        _require_admin(request)
        ok = package_manager.load_plugin(pkg_id)
        return {"success": ok, "pkg_id": pkg_id}

    @router.delete("/{pkg_id}")
    def remove_package(pkg_id: str, request: Request):
        """Completely remove a package: unload, delete files, remove DB record."""
        _require_admin(request)
        if not package_manager.get_package(pkg_id):
            raise HTTPException(status_code=404, detail=f"Package '{pkg_id}' not found")
        ok = package_manager.remove_package(pkg_id)
        return {"success": ok, "pkg_id": pkg_id}

    return router

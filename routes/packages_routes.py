"""Package manager API routes — install, list, toggle, remove packages."""
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.constants import BASE_DIR
from src.tool_security import owner_is_admin_or_single_user

# Bundled example packages shipped with the app (example-packages/<id>/). These
# are offered one-click in the UI so users don't have to hunt for .zip files.
_BUNDLED_DIR = Path(BASE_DIR) / "example-packages"


class ToggleBody(BaseModel):
    enable: bool


class InstallBundledBody(BaseModel):
    id: str


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

    @router.get("/available")
    def list_available(request: Request):
        """List the bundled example packages, flagging which are installed."""
        _require_admin(request)
        installed_ids = {p["id"] for p in package_manager.list_packages()}
        available = []
        if _BUNDLED_DIR.is_dir():
            for sub in sorted(_BUNDLED_DIR.iterdir()):
                manifest = sub / "manifest.json"
                if not (sub.is_dir() and manifest.exists()):
                    continue
                try:
                    with open(manifest, "r", encoding="utf-8") as f:
                        m = json.load(f)
                except Exception:
                    continue
                pid = m.get("id")
                if not pid:
                    continue
                available.append({
                    "id": pid,
                    "name": m.get("name", pid),
                    "version": m.get("version", ""),
                    "author": m.get("author"),
                    "description": m.get("description", ""),
                    "permissions": m.get("requiredPermissions", []),
                    "installed": pid in installed_ids,
                })
        return {"available": available, "count": len(available)}

    @router.post("/install-bundled")
    def install_bundled(body: InstallBundledBody, request: Request):
        """Install one of the bundled example packages by id. The package
        directory is zipped on the fly (so it can't drift from committed .zips)
        and run through the same security scan as an uploaded package."""
        owner = _require_admin(request)

        # Resolve strictly inside the bundled dir — reject any path games.
        pkg_dir = (_BUNDLED_DIR / body.id).resolve()
        if _BUNDLED_DIR.resolve() not in pkg_dir.parents or not pkg_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Bundled package '{body.id}' not found")
        if not (pkg_dir / "manifest.json").exists():
            raise HTTPException(status_code=404, detail=f"Bundled package '{body.id}' has no manifest")

        tmp_dir = tempfile.mkdtemp()
        try:
            zip_path = Path(tmp_dir) / f"{body.id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in pkg_dir.iterdir():
                    # Flat archive (manifest at root); skip any prebuilt zips.
                    if item.is_file() and item.suffix != ".zip":
                        zf.write(item, item.name)
            result = package_manager.install_package(str(zip_path), owner=owner)
            package_manager.load_plugin(result["package_id"])
            return JSONResponse(status_code=201, content=result)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Installation failed: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
            # Load the backend immediately so route registration happens now,
            # not only on the next server restart.
            package_manager.load_plugin(result["package_id"])
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

"""
PackageManager: Dynamic package installation and lifecycle management.
Packages are ZIP archives with manifest.json, optional backend (.py) and frontend (.js) components.
"""
import json
import logging
import shutil
import uuid
import zipfile
from pathlib import Path

from src.execution_guard import scan_package

logger = logging.getLogger(__name__)

MANIFEST_REQUIRED_FIELDS = {"id", "name", "version", "entryPoint"}

_SUPPORTED_PERMISSIONS = {
    "NET_ADMIN", "HardwareBridgeAccess", "DeviceMapping",
    "READ_METRICS", "WRITE_FILES", "MANAGE_WORKSPACES",
}

MANIFEST_SCHEMA = {
    "id": str,
    "name": str,
    "version": str,
    "entryPoint": str,
}


class PackageManager:
    def __init__(self, packages_dir: str, static_dir: str):
        self.packages_dir = Path(packages_dir)
        self.static_packages_dir = Path(static_dir) / "packages"
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.static_packages_dir.mkdir(parents=True, exist_ok=True)
        self._loaded_modules: dict = {}

    def install_package(self, zip_path: str, owner: str | None = None) -> dict:
        """
        Install a package from a .zip file.
        Returns {success, package_id, risk_level, warnings}.
        Does NOT auto-load — call load_plugin() if needed.
        """
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(f"ZIP not found: {zip_path}")
        if not zipfile.is_zipfile(zip_path):
            raise ValueError(f"Not a valid ZIP file: {zip_path}")

        tmp_dir = self.packages_dir / f"_tmp_{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Security: reject absolute paths and path traversal
                for name in zf.namelist():
                    if name.startswith("/") or ".." in name:
                        raise ValueError(f"Unsafe path in ZIP: {name}")
                zf.extractall(tmp_dir)

            # Locate manifest.json (root or one level deep)
            extract_root = tmp_dir
            manifest_path = tmp_dir / "manifest.json"
            if not manifest_path.exists():
                subdirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
                if subdirs:
                    candidate = subdirs[0] / "manifest.json"
                    if candidate.exists():
                        manifest_path = candidate
                        extract_root = subdirs[0]

            if not manifest_path.exists():
                raise ValueError("manifest.json not found in package")

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            self._validate_manifest(manifest)

            scan_result = scan_package(manifest, str(extract_root))
            if scan_result["blocked"]:
                raise PermissionError(f"Package blocked by security scan: {scan_result['reason']}")

            pkg_id = manifest["id"]
            install_dir = self.packages_dir / pkg_id

            if install_dir.exists():
                shutil.rmtree(install_dir)

            shutil.copytree(extract_root, install_dir)

            # Publish frontend widgets to /static/packages/<pkg_id>/
            frontend_hooks = manifest.get("frontendHooks", {})
            if frontend_hooks:
                static_pkg_dir = self.static_packages_dir / pkg_id
                static_pkg_dir.mkdir(exist_ok=True)
                for _hook_type, widget_path in frontend_hooks.items():
                    widget_file = install_dir / widget_path
                    if widget_file.exists():
                        shutil.copy2(widget_file, static_pkg_dir / Path(widget_path).name)

            self._save_to_db(manifest, install_dir, scan_result, owner)

            logger.info(f"Package installed: {pkg_id} v{manifest.get('version')} risk={scan_result['risk']}")
            return {
                "success": True,
                "package_id": pkg_id,
                "risk_level": scan_result["risk"],
                "warnings": scan_result.get("warnings", []),
            }
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _validate_manifest(self, manifest: dict):
        missing = MANIFEST_REQUIRED_FIELDS - manifest.keys()
        if missing:
            raise ValueError(f"manifest.json missing required fields: {missing}")

        for field, expected_type in MANIFEST_SCHEMA.items():
            if field in manifest and not isinstance(manifest[field], expected_type):
                raise ValueError(f"manifest.json: field '{field}' must be {expected_type.__name__}")

        unknown_perms = set(manifest.get("requiredPermissions", [])) - _SUPPORTED_PERMISSIONS
        if unknown_perms:
            logger.warning(f"Unknown permissions in manifest (will be ignored): {unknown_perms}")

    def _save_to_db(self, manifest: dict, install_dir: Path, scan_result: dict, owner: str | None):
        from core.database import get_db_session, Package
        with get_db_session() as db:
            existing = db.query(Package).filter(Package.id == manifest["id"]).first()
            if existing:
                existing.version = manifest.get("version", "1.0.0")
                existing.author = manifest.get("author")
                existing.description = manifest.get("description")
                existing.status = "installed"
                existing.risk_level = scan_result["risk"]
                existing.install_path = str(install_dir)
                existing.permissions = manifest.get("requiredPermissions", [])
                existing.frontend_hooks = manifest.get("frontendHooks", {})
                existing.entry_point = manifest.get("entryPoint", "")
            else:
                db.add(Package(
                    id=manifest["id"],
                    name=manifest["name"],
                    version=manifest.get("version", "1.0.0"),
                    author=manifest.get("author"),
                    description=manifest.get("description"),
                    status="installed",
                    permissions=manifest.get("requiredPermissions", []),
                    entry_point=manifest.get("entryPoint", ""),
                    frontend_hooks=manifest.get("frontendHooks", {}),
                    install_path=str(install_dir),
                    risk_level=scan_result["risk"],
                    owner=owner,
                ))

    def load_plugin(self, pkg_id: str) -> bool:
        """Dynamically import a package's Python backend into the process."""
        import importlib.util
        from core.database import get_db_session, Package

        with get_db_session() as db:
            pkg = db.query(Package).filter(Package.id == pkg_id).first()
            if not pkg or pkg.status == "disabled":
                return False
            entry = pkg.entry_point
            install_path = pkg.install_path

        if not entry or not entry.endswith(".py"):
            return True  # No Python backend

        module_file = Path(install_path) / entry
        if not module_file.exists():
            logger.warning(f"Plugin entry point not found: {module_file}")
            return False

        try:
            spec = importlib.util.spec_from_file_location(f"pkg_{pkg_id}", module_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._loaded_modules[pkg_id] = module
            logger.info(f"Plugin loaded: {pkg_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to load plugin {pkg_id}: {e}")
            return False

    def unload_plugin(self, pkg_id: str) -> bool:
        """Remove a plugin from memory (does not uninstall)."""
        self._loaded_modules.pop(pkg_id, None)
        logger.info(f"Plugin unloaded: {pkg_id}")
        return True

    def toggle_plugin(self, pkg_id: str, enable: bool) -> bool:
        """Enable or disable a package at runtime without uninstalling."""
        from core.database import get_db_session, Package
        with get_db_session() as db:
            pkg = db.query(Package).filter(Package.id == pkg_id).first()
            if not pkg:
                return False
            pkg.status = "installed" if enable else "disabled"

        if enable:
            self.load_plugin(pkg_id)
        else:
            self.unload_plugin(pkg_id)
        return True

    def remove_package(self, pkg_id: str) -> bool:
        """Permanently remove: unload, delete files, remove DB record."""
        self.unload_plugin(pkg_id)

        from core.database import get_db_session, Package
        install_dir = None
        with get_db_session() as db:
            pkg = db.query(Package).filter(Package.id == pkg_id).first()
            if pkg:
                install_dir = Path(pkg.install_path) if pkg.install_path else None
                db.delete(pkg)

        if install_dir and install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)

        static_pkg_dir = self.static_packages_dir / pkg_id
        if static_pkg_dir.exists():
            shutil.rmtree(static_pkg_dir, ignore_errors=True)

        logger.info(f"Package removed: {pkg_id}")
        return True

    def list_packages(self, owner: str | None = None) -> list:
        from core.database import get_db_session, Package
        with get_db_session() as db:
            q = db.query(Package)
            if owner:
                q = q.filter(Package.owner == owner)
            return [self._pkg_to_dict(p) for p in q.all()]

    def get_package(self, pkg_id: str) -> dict | None:
        from core.database import get_db_session, Package
        with get_db_session() as db:
            pkg = db.query(Package).filter(Package.id == pkg_id).first()
            return self._pkg_to_dict(pkg) if pkg else None

    def get_all_frontend_hooks(self) -> dict:
        """Aggregate all active frontend hook widgets across installed packages."""
        from core.database import get_db_session, Package
        hooks = {"sidebarWidget": [], "toolbar": [], "settingsTab": [], "chatPanel": []}
        with get_db_session() as db:
            pkgs = db.query(Package).filter(Package.status == "installed").all()
            for pkg in pkgs:
                for hook_type, widget_path in (pkg.frontend_hooks or {}).items():
                    widget_name = Path(widget_path).name
                    url = f"/static/packages/{pkg.id}/{widget_name}"
                    if hook_type in hooks:
                        hooks[hook_type].append({"pkg_id": pkg.id, "name": pkg.name, "url": url})
                    else:
                        hooks.setdefault(hook_type, []).append({"pkg_id": pkg.id, "name": pkg.name, "url": url})
        return hooks

    def _pkg_to_dict(self, pkg) -> dict:
        return {
            "id": pkg.id,
            "name": pkg.name,
            "version": pkg.version,
            "author": pkg.author,
            "description": pkg.description,
            "status": pkg.status,
            "permissions": pkg.permissions or [],
            "entry_point": pkg.entry_point,
            "frontend_hooks": pkg.frontend_hooks or {},
            "risk_level": pkg.risk_level,
            "owner": pkg.owner,
            "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
            "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
        }

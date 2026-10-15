"""OXS admin REST surface.

Exposed at ``/api/oxs/*`` (admin-only). The companion inbound webhook
surface lives at ``/api/ext/<id>/webhook/<token>`` and is wired directly
into the FastAPI app from ``app.py`` (not through this router) so the
two paths cannot accidentally share auth context.

Routes:
  GET    /api/oxs/extensions                 — list installed extensions
  GET    /api/oxs/extensions/{id}           — single extension detail
  POST   /api/oxs/extensions/{id}/enable    — enable
  POST   /api/oxs/extensions/{id}/disable   — disable
  POST   /api/oxs/extensions/{id}/reload    — re-import entry-point
  POST   /api/oxs/extensions/{id}/uninstall — remove from registry
  POST   /api/oxs/rescan                     — discover new extensions
  GET    /api/oxs/extensions/{id}/webhooks   — list installed webhooks + raw token
  GET    /api/oxs/extensions/{id}/audit      — extension audit entries
  GET    /api/oxs/audit                      — global audit tail
  GET    /api/oxs/health                     — readiness probe
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.extensions.audit import audit_query
from core.extensions import webhooks as oxs_webhooks


# Pydantic request models are declared at module level (not inside
# ``setup_oxs_api_routes``) so FastAPI's introspector can resolve them as
# request bodies. Models defined inside a nested function are mis-detected
# as query parameters, which made the POST routes return 422.

class _GitInstallBody(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class _SidebarVisibleBody(BaseModel):
    visible: bool

# Module-level admin check. Closure (called by tests) resolves this via
# ``sys.modules[__name__]._require_admin`` so a test-time monkeypatch is
# honored on the next request. The default delegates to the real
# auth layer; tests swap this for a header-based stub.
def _require_admin(request: Request) -> str:  # noqa: F811
    from core.middleware import require_admin as _real
    return _real(request)

logger = logging.getLogger(__name__)


def setup_oxs_api_routes(registry) -> APIRouter:
    """Build the admin router bound to ``registry``."""
    router = APIRouter(prefix="/api/oxs", tags=["oxs"])

    # ── helpers ─────────────────────────────────────────────────────────

    def _require_admin(request: Request) -> str:
        """Default admin check.

        Looks up the module-level ``oxs_api._require_admin`` at call time
        (Python late-binding lookup in the closure's enclosing scope)
        so tests can replace the module-level reference and have the
        closure pick it up on the next request.
        """
        # Resolve from the module namespace, not local scope, so the
        # monkeypatch in tests is visible.
        import sys as _sys_mod
        mod = _sys_mod.modules[__name__]
        return mod._require_admin(request)

    def _ext(id_: str):
        running = registry.get(id_)
        if running is None:
            raise HTTPException(404, f"extension not found: {id_!r}")
        return running

    def _summary(running) -> dict:
        host = running.host
        return {
            "id": running.extension_id,
            "name": running.manifest["name"],
            "version": running.manifest["version"],
            "source": running.source,
            "is_enabled": running.enabled,
            "sidebar_visible": bool(getattr(running, "sidebar_visible", False)),
            "installed_by": running.manifest.get("installed_by"),
            "install_error": running.manifest.get("install_error"),
            "capabilities": sorted(host._declared_capabilities.keys()),
            "privileged": sorted(host._granted_privileged.keys()),
            "violations": list(host.violations),
            "panels": [
                {"id": p.id, "title": p.title, "source_url": p.source_url}
                for p in (host.panels or [])
            ],
            "settings_tabs": [
                {"tab": st.tab, "title": st.title, "source_url": st.source_url}
                for st in (host.settings_tabs or [])
            ],
            "stats": {
                "tools": len(host.tools),
                "mcp_tools": len(host.mcp_tools),
                "routes": len(host.routes),
                "webhooks": len(host.webhooks),
                "event_subs": len(host.event_subs),
                "task_actions": len(host.task_actions),
                "statics": len(host.statics),
            },
        }

    # ── routes ─────────────────────────────────────────────────────────

    @router.get("/health")
    def health():
        return {"ok": True}

    @router.get("/extensions")
    def list_extensions(request: Request):
        _require_admin(request)
        return {
            "extensions": [_summary(r) for r in registry.list()],
        }

    @router.get("/extensions/{ext_id}")
    def get_extension(ext_id: str, request: Request):
        _require_admin(request)
        return _summary(_ext(ext_id))

    @router.post("/extensions/{ext_id}/enable")
    def enable_extension(ext_id: str, request: Request):
        operator = _require_admin(request)
        _ext(ext_id)  # raises 404 if missing
        running = registry.enable(ext_id, operator=operator)
        return {"ok": True, "is_enabled": running.enabled}

    @router.post("/extensions/{ext_id}/disable")
    def disable_extension(ext_id: str, request: Request):
        operator = _require_admin(request)
        _ext(ext_id)
        registry.disable(ext_id, operator=operator)
        return {"ok": True, "is_enabled": False}

    @router.post("/extensions/{ext_id}/reload")
    def reload_extension(ext_id: str, request: Request):
        operator = _require_admin(request)
        _ext(ext_id)
        running = registry.reload(ext_id, operator=operator)
        return {"ok": True, "is_enabled": running.enabled}

    @router.post("/extensions/{ext_id}/uninstall")
    def uninstall_extension(ext_id: str, request: Request):
        operator = _require_admin(request)
        _ext(ext_id)
        registry.uninstall(ext_id, operator=operator)
        return {"ok": True}

    @router.post("/rescan")
    def rescan(request: Request):
        _require_admin(request)
        found = registry.rescan()
        return {"ok": True, "count": len(found)}

    @router.post("/install/git")
    def install_from_git(payload: _GitInstallBody, request: Request):
        """Clone a public https git repo into ``data/extensions/<id>/``."""
        operator = _require_admin(request)
        try:
            running = registry.install_from_git(payload.url, operator=operator)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
        return {
            "ok": True,
            "id": running.extension_id,
            "version": running.manifest.get("version"),
            "is_enabled": running.enabled,
        }

    @router.post("/install/zip")
    async def install_from_zip(request: Request):
        """Accept a multipart ``file=`` upload of an OXS zip and install it."""
        operator = _require_admin(request)
        # Use python-multipart via FastAPI's form parsing
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(400, "missing 'file' field in multipart upload")
        # Spool to a temp file so the existing zip_path-based installer can
        # stream it (it uses ZipFile + zip-slip checks).
        import tempfile as _tempfile
        # Write upload to a temp file
        tmp_zip = _tempfile.NamedTemporaryFile(prefix="oxs-upload-", suffix=".zip", delete=False)
        try:
            while True:
                chunk = await upload.read(65536)
                if not chunk:
                    break
                tmp_zip.write(chunk)
            tmp_zip.flush()
            tmp_zip.close()
            try:
                running = registry.install_from_zip(tmp_zip.name, operator=operator)
            except (ValueError, RuntimeError, FileNotFoundError) as e:
                raise HTTPException(400, str(e))
        finally:
            try:
                os.unlink(tmp_zip.name)
            except OSError:
                pass
        return {
            "ok": True,
            "id": running.extension_id,
            "version": running.manifest.get("version"),
            "is_enabled": running.enabled,
        }

    @router.get("/extensions/{ext_id}/webhooks")
    def list_webhooks(ext_id: str, request: Request):
        _require_admin(request)
        _ext(ext_id)  # raises 404 if missing
        records = oxs_webhooks.list_for_extension(ext_id)
        # Include the raw token ONCE. Operators should rotate after
        # viewing. The token is also returned on install.
        return {
            "webhooks": [
                {
                    "path_suffix": r.path_suffix,
                    "event_filter": list(r.event_filter),
                    "raw_token": oxs_webhooks.get_raw_token(r),
                    "registered_at": r.registered_at.isoformat(),
                }
                for r in records
            ],
        }

    @router.get("/extensions/{ext_id}/audit")
    def extension_audit(ext_id: str, request: Request, limit: int = 100):
        _require_admin(request)
        return {"events": audit_query(extension_id=ext_id, limit=limit)}

    @router.get("/audit")
    def global_audit(request: Request, limit: int = 100):
        _require_admin(request)
        return {"events": audit_query(limit=limit)}

    # ── Sidebar (any logged-in user) ─────────────────────────────────────

    def _require_user(request: Request) -> str:
        """Any logged-in user. No admin gate."""
        user = getattr(request.state, "current_user", None)
        if not user:
            raise HTTPException(401, "Not authenticated")
        return user

    @router.get("/sidebar")
    def sidebar_extensions(request: Request):
        """Public list of extensions the operator has surfaced in the sidebar."""
        _require_user(request)
        items = []
        for r in registry.list():
            if not getattr(r, "sidebar_visible", False):
                continue
            items.append({
                "id": r.extension_id,
                "name": r.manifest["name"],
                "version": r.manifest["version"],
                "is_enabled": r.enabled,
                "capabilities": sorted(r.host._declared_capabilities.keys()),
            })
        return {"extensions": items}

    # ── Sidebar visibility toggle (admin) ────────────────────────────────

    @router.post("/extensions/{ext_id}/sidebar-visible")
    def set_sidebar_visible(ext_id: str, payload: _SidebarVisibleBody, request: Request):
        operator = _require_admin(request)
        try:
            running = registry.set_sidebar_visible(ext_id, payload.visible, operator=operator)
        except KeyError as e:
            raise HTTPException(404, str(e))
        return {"ok": True, "id": running.extension_id, "sidebar_visible": running.sidebar_visible}

    return router


# ── Inbound webhook handler ───────────────────────────────────────────────
#
# This route lives on the FastAPI app directly (not on the router
# above) so it doesn't carry the /api/oxs prefix and so the AUTH_EXEMPT
# pattern matches it cleanly. PR 4 wires it from ``app.py``.

from fastapi import APIRouter as _APIRouter  # noqa: E402

inbound_webhook_router = _APIRouter(prefix="/api/ext", tags=["oxs-webhook"])


def setup_oxs_inbound_webhooks() -> _APIRouter:
    """Build the inbound webhook router. Mount on the FastAPI app."""
    from fastapi import Request

    @inbound_webhook_router.post("/{ext_id}/webhook/{token}")
    async def receive_webhook(ext_id: str, token: str, request: Request):
        """Inbound webhook for an OXS extension.

        The token in the URL is the credential. We validate it constant-
        time against the hashed token in the registry, then dispatch to
        the extension's handler in a background task.
        """
        # Read body and headers
        body = await request.body()
        headers = dict(request.headers)
        query = dict(request.query_params)

        from core.extensions.webhooks import dispatch as webhook_dispatch
        status, message = await webhook_dispatch(
            ext_id, token, body, headers=headers, query=query,
        )
        # dispatch returns an int status; map to HTTPException for the
        # 404 / 401 / 429 cases.
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status,
            content={"status": status, "message": message},
        )

    return inbound_webhook_router


__all__ = ["setup_oxs_api_routes", "setup_oxs_inbound_webhooks"]
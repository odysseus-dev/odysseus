"""Disable Cookbook serve lifecycle and block legacy serve HTTP routes."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import FrozenSet, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("titan.remove-serve")

GONE_ROUTES: FrozenSet[Tuple[str, str]] = frozenset({
    ("POST", "/api/model/serve"),
    ("POST", "/api/cookbook/rebuild-engine"),
    ("GET", "/api/cookbook/tasks/status"),
    ("POST", "/api/codex/cookbook/serve"),
})

GONE_PATH_PREFIXES: Tuple[str, ...] = (
    "/api/codex/cookbook/stop/",
)

KEEP_ENDPOINT_IDS: FrozenSet[str] = frozenset({
    "titan-llm-host",
    "titan-sd-scheduler",
    "titan-vision-host",
})

_STALE_URL_RE = re.compile(
    r":8100\b|diffusion_server|animagine|/serve-|cookbook.*serve",
    re.I,
)

_GONE_BODY = {
    "detail": "Cookbook serve was removed. Use Titan Model Hub or POST /api/titan/hub/load.",
    "removed": True,
}


def preload_patches() -> None:
    """Call before app import so startup does not spawn serve lifecycle."""
    try:
        from titan.patches.model_routes_dedupe import apply_model_routes_dedupe_patch

        apply_model_routes_dedupe_patch()
    except Exception as exc:
        log.warning("model_routes dedupe preload skipped: %s", exc)

    try:
        import src.cookbook_serve_lifecycle as lifecycle

        async def _noop_lifecycle() -> None:
            return

        lifecycle.cookbook_serve_lifecycle_loop = _noop_lifecycle
        log.debug("cookbook_serve_lifecycle_loop disabled")
    except Exception as exc:
        log.warning("lifecycle preload skipped: %s", exc)

    try:
        import src.builtin_actions as builtin_actions

        async def _cookbook_serve_removed(*_args, **_kwargs):
            return (
                "Cookbook serve was removed. Use VRAM Scheduler panel or Model Hub "
                "or POST /api/titan/hub/load.",
                False,
            )

        if "cookbook_serve" in builtin_actions.BUILTIN_ACTIONS:
            builtin_actions.BUILTIN_ACTIONS["cookbook_serve"] = _cookbook_serve_removed
            log.debug("builtin cookbook_serve disabled")
    except Exception as exc:
        log.warning("builtin_actions preload skipped: %s", exc)


def route_is_gone(method: str, path: str) -> bool:
    m = (method or "GET").upper()
    p = path or ""
    if (m, p) in GONE_ROUTES:
        return True
    return any(p.startswith(prefix) for prefix in GONE_PATH_PREFIXES)


def purge_stale_endpoints() -> list[str]:
    from core.database import ModelEndpoint, SessionLocal

    removed: list[str] = []
    db = SessionLocal()
    try:
        for ep in db.query(ModelEndpoint).all():
            eid = ep.id or ""
            url = ep.base_url or ""
            name = ep.name or ""
            if eid in KEEP_ENDPOINT_IDS:
                continue
            stale = (
                eid.startswith("img-")
                or ":8100" in url
                or _STALE_URL_RE.search(url or "")
                or _STALE_URL_RE.search(name or "")
                or (
                    url.rstrip("/") == "http://host.docker.internal:8000/v1"
                    and eid != "titan-llm-host"
                )
                or (
                    (ep.model_type or "") == "image"
                    and eid not in KEEP_ENDPOINT_IDS
                )
                or (
                    (ep.model_type or "") == "llm"
                    and eid not in KEEP_ENDPOINT_IDS
                    and "host.docker.internal:8000" in url
                )
            )
            if stale:
                removed.append(f"{eid} ({name} {url})")
                db.delete(ep)
        if removed:
            db.commit()
            log.info("Removed %d stale cookbook endpoints", len(removed))
        return removed
    except Exception as exc:
        db.rollback()
        log.warning("Endpoint purge failed: %s", exc)
        return []
    finally:
        db.close()


def prune_cookbook_state() -> dict:
    from src.constants import COOKBOOK_STATE_FILE

    path = Path(COOKBOOK_STATE_FILE)
    if not path.is_file():
        return {"skipped": "no state file"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)}

    tasks = data.get("tasks") or []
    before_tasks = len(tasks)
    data["tasks"] = [t for t in tasks if (t or {}).get("type") != "serve"]

    presets = data.get("presets") or []
    before_presets = len(presets)
    data["presets"] = []

    try:
        from core.atomic_io import atomic_write_json

        atomic_write_json(path, data)
    except Exception:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return {
        "tasks_removed": before_tasks - len(data["tasks"]),
        "presets_removed": before_presets,
    }


def apply_removal() -> None:
    endpoints = purge_stale_endpoints()
    state = prune_cookbook_state()
    log.info(
        "Cookbook serve stack removed (endpoints=%s, state=%s)",
        endpoints or "none",
        state,
    )


class TitanCookbookServeGoneMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if route_is_gone(request.method, request.url.path):
            return JSONResponse(status_code=410, content=_GONE_BODY)
        return await call_next(request)


class TitanCookbookRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "GET" and request.url.path == "/cookbook":
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/model-hub", status_code=302)
        return await call_next(request)


def register_serve_middleware(app) -> None:
    app.add_middleware(TitanCookbookServeGoneMiddleware)
    app.add_middleware(TitanCookbookRedirectMiddleware)

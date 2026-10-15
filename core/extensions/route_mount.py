"""route_mount.py — mount extension-declared routes on the FastAPI app.

Each call to :meth:`OXSHost.add_route` stores a ``RouteRegistration`` on
the host. The registry's ``_wire`` (on enable) iterates those records and
calls :func:`install` here, which delegates to ``app.add_api_route`` with
an auth check based on the route's declared ``auth`` level:

  - ``"public"`` — no auth check
  - ``"user"``   — must be logged in (request.state.current_user is set)
  - ``"admin"``  — must be admin (``is_admin(current_user) == True``)

Auth is enforced via FastAPI's ``dependencies`` parameter (a Depends()
that returns the current user or raises 401/403). This way the handler
keeps its original signature and FastAPI can still introspect it
correctly — we don't have to wrap the handler in a custom signature
(which broke introspection for handlers that expect ``request`` as a
positional arg).

Sync handlers are run in a thread via ``asyncio.to_thread`` so they don't
block the event loop.

Uninstall walks the recorded (ext_id, path) pairs and filters the
matching routes out of ``app.router.routes``. FastAPI does not expose a
clean per-route removal API, so we tag each mounted APIRoute with the
extension_id and filter the router's route list at uninstall time.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, Dict, List, Tuple

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


# path -> {ext_id, auth, methods}
_mounted: Dict[str, Dict[str, Any]] = {}


def list_mounted() -> List[Dict[str, Any]]:
    return [
        {"extension_id": rec["ext_id"], "path": p}
        for p, rec in _mounted.items()
    ]


def reset_for_tests() -> None:
    _mounted.clear()


def _auth_dependency(auth: str):
    """Build a FastAPI dependency that enforces the route's auth level."""
    def _dep(request: Request) -> str:
        if auth == "public":
            return ""
        user = getattr(request.state, "current_user", None)
        if not user:
            raise HTTPException(401, "Not authenticated")
        if auth == "admin":
            from core.middleware import auth_disabled
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if not auth_disabled() and (not auth_mgr or not auth_mgr.is_admin(user)):
                raise HTTPException(403, "Admin only")
        return user
    return _dep


def _wrap_async(raw: Callable) -> Callable:
    """Coerce a sync handler into an async one (thread-pool dispatch).

    The wrapper is constructed via :func:`functools.wraps` so the
    original signature (including ``Request`` annotation, path-param
    names, and defaults) is what FastAPI sees — this is critical for
    path-param resolution and Request detection. Sync handlers run in a
    thread so they don't block the event loop.
    """
    if inspect.iscoroutinefunction(raw):
        return raw

    import functools as _ft
    sig = inspect.signature(raw)

    @_ft.wraps(raw)
    async def _async(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return await asyncio.to_thread(raw, *bound.args, **bound.kwargs)

    # functools.wraps copies __wrapped__, __name__, __doc__ but NOT
    # __signature__ — copy it explicitly so FastAPI introspection sees
    # the original params, not (*args, **kwargs).
    _async.__signature__ = sig  # type: ignore[attr-defined]
    return _async


def install(
    app: Any,
    extension_id: str,
    path: str,
    handler: Callable,
    *,
    auth: str = "user",
    methods: Tuple[str, ...] = ("GET",),
) -> None:
    """Mount ``handler`` at ``path`` on the FastAPI ``app``.

    Idempotent: a second call with the same ``(extension_id, path, methods)``
    replaces the previous mount. The new route is tagged with
    ``extension_id`` and ``_ext_methods`` so :func:`uninstall` can find it.
    """
    # Remove any previous mount for the same (ext_id, path, methods)
    # before re-adding — but ONLY that exact method set, not the other
    # methods on the same path. Uninstalling by path alone would wipe
    # out unrelated siblings (e.g. GET when we're reinstalling POST).
    uninstall(app, extension_id, path=path, methods=set(methods))

    async_handler = _wrap_async(handler)
    route_name = f"oxs_{extension_id}_{path}_{'_'.join(methods)}".replace("/", "_")
    app.add_api_route(
        path,
        async_handler,
        methods=list(methods),
        dependencies=[Depends(_auth_dependency(auth))],
        name=route_name,
    )
    # Tag the just-added route so uninstall can find it precisely.
    for r in app.router.routes:
        if getattr(r, "name", "") == route_name:
            r.extension_id = extension_id
            r._ext_methods = frozenset({m.upper() for m in methods})
            break
    _mounted[path] = {
        "ext_id": extension_id, "auth": auth,
        "methods": list(methods), "name": route_name,
    }
    logger.info(
        "OXS mounted %s %s for extension %s (auth=%s)",
        ",".join(methods), path, extension_id, auth,
    )


def uninstall(
    app: Any,
    extension_id: str,
    *,
    path: str | None = None,
    methods: set | None = None,
) -> int:
    """Remove one or every route mounted for ``extension_id``.

    Filters by ``path`` and (optionally) ``methods`` so we can wipe just
    one method on a path without disturbing siblings. Returns the count
    of routes removed.
    """
    try:
        router = app.router
    except AttributeError:
        router = None

    # Update _mounted bookkeeping. We track per (path, methods) but
    # multiple methods on the same path are stored under one key, so
    # to avoid losing track we only pop if either path is None OR
    # the path matches AND the recorded methods match what we're
    # being asked to remove.
    if path is not None and methods is not None:
        rec = _mounted.get(path)
        if rec and rec["ext_id"] == extension_id and set(rec["methods"]) == methods:
            _mounted.pop(path, None)
    elif path is not None:
        # Remove only if the recorded methods match (caller didn't
        # specify methods; treat as exact path match).
        rec = _mounted.get(path)
        if rec and rec["ext_id"] == extension_id:
            _mounted.pop(path, None)

    if router is None:
        return 0

    def _belongs(rec: Any) -> bool:
        if getattr(rec, "extension_id", None) != extension_id:
            return False
        if path is not None and getattr(rec, "path", "") != path:
            return False
        if methods is not None:
            return getattr(rec, "_ext_methods", None) == frozenset({m.upper() for m in methods})
        return True

    before = len(router.routes)
    router.routes = [r for r in router.routes if not _belongs(r)]
    removed = before - len(router.routes)
    if removed:
        logger.info(
            "OXS unmounted %d route(s) for extension %s",
            removed, extension_id,
        )
    return removed

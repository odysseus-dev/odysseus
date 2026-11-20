"""Codex cookbook bridges look up sibling routes (/api/model/serve, /api/model/cached)
on the running app. FastAPI >= 0.14x keeps included routers nested instead of
flattening them into ``app.routes``, so the lookup must descend into them."""

from fastapi import APIRouter, FastAPI

from routes.codex_routes import _find_app_endpoint


def test_finds_route_in_included_router_with_prefix():
    router = APIRouter()

    @router.get("/model/cached")
    async def model_cached():
        return {}

    @router.post("/model/serve")
    async def model_serve():
        return {}

    app = FastAPI()
    app.include_router(router, prefix="/api")

    assert _find_app_endpoint(app.routes, "GET", "/api/model/cached") is model_cached
    assert _find_app_endpoint(app.routes, "POST", "/api/model/serve") is model_serve


def test_method_and_path_must_both_match():
    router = APIRouter()

    @router.get("/api/model/cached")
    async def model_cached():
        return {}

    app = FastAPI()
    app.include_router(router)

    assert _find_app_endpoint(app.routes, "POST", "/api/model/cached") is None
    assert _find_app_endpoint(app.routes, "GET", "/model/cached") is None

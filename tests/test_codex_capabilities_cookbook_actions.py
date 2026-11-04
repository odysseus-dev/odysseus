"""Capabilities must advertise every cookbook action that has a route.

The skill docs tell agents to check /api/codex/capabilities before using a
surface, so an action missing from this list is invisible to them.
"""

from starlette.requests import Request

from routes import codex_routes


def _endpoint(router, method: str, path: str):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _request(scopes: list[str]) -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/codex/capabilities",
        "headers": [],
        "state": {},
    })
    request.state.current_user = "api"
    request.state.api_token = True
    request.state.api_token_owner = "alice"
    request.state.api_token_scopes = scopes
    return request


def test_capabilities_cookbook_actions_match_routes():
    router = codex_routes.setup_codex_routes()
    capabilities = _endpoint(router, "GET", "/api/codex/capabilities")

    advertised = set(capabilities(_request(["cookbook:read"]))["tools"]["cookbook"]["actions"])

    routed = set()
    for route in router.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/codex/cookbook/"):
            routed.add(path[len("/api/codex/cookbook/"):].split("/")[0])

    assert advertised == routed

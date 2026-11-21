"""The app-level middlewares must classify the same path Starlette routes.

`core/middleware.get_application_route_path` exists because uvicorn prefixes
`scope["path"]` with a configured ASGI `root_path` while Starlette strips it
before matching routes. `AuthMiddleware` and `SecurityHeadersMiddleware` follow
that rule; the three middlewares defined in `app.py` read `request.url.path`,
which still carries the prefix, so behind the reverse-proxy mount SECURITY.md
recommends (`--root-path /odysseus`) their prefix lists stopped matching.
"""

import ast
import re
from pathlib import Path

import pytest

from core.middleware import get_application_route_path
from src.interactive_gate import should_track_interactive_request

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")

MIDDLEWARES = (
    "_RequestTimeoutMiddleware",
    "_InteractiveActivityMiddleware",
    "_SlowRequestLogMiddleware",
)


def _middleware_node(name):
    for node in ast.walk(ast.parse(APP_SOURCE)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in app.py")


def _reads_raw_request_path(node):
    """True if the class actually evaluates `request.url.path` anywhere.

    Checked on the AST rather than the text so a comment naming the attribute —
    which is exactly how the fix documents itself — is not mistaken for a use.
    """
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and child.attr == "path"
            and isinstance(child.value, ast.Attribute)
            and child.value.attr == "url"
        ):
            return True
    return False


def _calls(node, function_name):
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == function_name
        for child in ast.walk(node)
    )


@pytest.mark.parametrize("name", MIDDLEWARES)
def test_middleware_uses_the_application_route_path(name):
    node = _middleware_node(name)
    assert _calls(node, "get_application_route_path"), (
        f"{name} must resolve the path with get_application_route_path()"
    )
    assert not _reads_raw_request_path(node), (
        f"{name} must not read request.url.path — it carries the root_path prefix"
    )


# ── What the raw path actually breaks ──


def _timeout_exempt_prefixes():
    namespace = {}
    match = re.search(r"^_TIMEOUT_EXEMPT_PREFIXES = \(.*?^\)", APP_SOURCE, re.S | re.M)
    assert match, "_TIMEOUT_EXEMPT_PREFIXES not found"
    exec(compile(match.group(0), "<app>", "exec"), namespace)  # noqa: S102
    return namespace["_TIMEOUT_EXEMPT_PREFIXES"]


@pytest.mark.parametrize("prefix", _timeout_exempt_prefixes())
def test_timeout_exemptions_survive_a_root_path_mount(prefix):
    """A mounted deployment must keep its long-running routes exempt.

    `/api/upload` and friends are exempt because they legitimately outrun the
    hard timeout. Behind a mount the raw path is `/odysseus/api/upload`, which
    matches no prefix, so every one of them became subject to a 504.
    """
    route = f"{prefix}/x"
    scope = {"path": f"/odysseus{route}", "root_path": "/odysseus"}

    assert get_application_route_path(scope) == route
    assert any(get_application_route_path(scope).startswith(p) for p in _timeout_exempt_prefixes())
    # The raw path is what used to be matched, and it matches nothing.
    assert not any(scope["path"].startswith(p) for p in _timeout_exempt_prefixes())


@pytest.mark.parametrize("passive", ["/api/health", "/api/version", "/static/app.js"])
def test_passive_polls_stay_passive_behind_a_mount(passive):
    """Otherwise a health poll reads as foreground traffic.

    `_InteractiveActivityMiddleware` stops background tasks for every request
    the gate calls interactive, so misclassifying a poll does not merely lose
    tracking — it kills background work on each one.
    """
    scope = {"path": f"/odysseus{passive}", "root_path": "/odysseus"}
    resolved = get_application_route_path(scope)

    assert resolved == passive
    if not should_track_interactive_request(passive, "GET"):
        assert not should_track_interactive_request(resolved, "GET")


def test_unmounted_deployments_are_unchanged():
    scope = {"path": "/api/upload", "root_path": ""}
    assert get_application_route_path(scope) == "/api/upload"

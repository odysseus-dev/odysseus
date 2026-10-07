"""AI Reply may use its provider deadline without exempting other mail requests."""

import ast
import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def timeout_middleware():
    """Load the actual middleware without starting the application or pollers."""
    tree = ast.parse((Path(__file__).parents[1] / "app.py").read_text())
    relevant = [
        node for node in tree.body
        if (isinstance(node, ast.ClassDef) and node.name == "_RequestTimeoutMiddleware")
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id in {"_TIMEOUT_EXEMPT_PREFIXES", "_TIMEOUT_EXEMPT_PATHS"}
            for target in node.targets
        ))
    ]
    namespace = {
        "_BaseHTTPMiddleware": BaseHTTPMiddleware,
        "_asyncio": asyncio,
        "_JSONResponse": JSONResponse,
        "REQUEST_HARD_TIMEOUT": 0.01,
        "_TIMEOUT_EXEMPT_PATHS": set(),
    }
    exec(compile(ast.Module(body=relevant, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["_RequestTimeoutMiddleware"](lambda *_args: None)


@pytest.mark.parametrize("path,expected", [
    ("/api/email/ai-reply", 200),
    ("/api/email/ai-reply-extra", 504),
    ("/api/email/send", 504),
    ("/api/email/list", 504),
])
async def test_reply_can_use_provider_deadline_while_other_mail_keeps_guard(path, expected):
    request = Request({"type": "http", "path": path, "headers": [], "method": "POST"})

    async def slow_handler(_request):
        await asyncio.sleep(0.03)
        return JSONResponse({"success": True})

    response = await timeout_middleware().dispatch(request, slow_handler)
    assert response.status_code == expected

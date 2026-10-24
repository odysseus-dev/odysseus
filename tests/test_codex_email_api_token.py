"""Codex routes must call wrapped FastAPI handlers with plain values.

Regression for ``GET /api/codex/emails`` returning 401 "Invalid API token"
for a valid ``email:read`` bearer token while other Codex routes worked.

Root cause: the Codex wrappers call the email/document route handlers as
plain Python functions. Any parameter they did not pass kept its FastAPI
default (``Query(None)``, ``Query(0)``...), a truthy ``FieldInfo`` object, so
``list_emails`` ran ``datetime.strptime(<FieldInfo>, ...)`` and raised
``TypeError``. ``AuthMiddleware`` then masked that downstream exception as an
auth failure. Both halves are pinned here.
"""
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Query, Request
from fastapi.testclient import TestClient
from pydantic.fields import FieldInfo

from routes.codex_routes import _call_handler, setup_codex_routes

ROOT = Path(__file__).resolve().parents[1]


def _require_owner():  # pragma: no cover - never resolved for direct calls
    raise AssertionError("Depends(require_owner) must not be resolved by Codex")


def _assert_plain(kwargs: dict) -> None:
    leaked = {k: type(v).__name__ for k, v in kwargs.items() if isinstance(v, FieldInfo)}
    assert not leaked, f"FastAPI param defaults leaked into handler call: {leaked}"


def _email_router(calls: dict) -> APIRouter:
    """Mirror the real routes/email/email_routes.py handler signatures."""
    router = APIRouter(prefix="/api/email")

    @router.get("/list")
    async def list_emails(
        folder: str = Query("INBOX"),
        limit: int = Query(50),
        offset: int = Query(0),
        filter: str = Query("all"),
        from_addr: str | None = Query(None, alias="from"),
        account_id: str | None = Query(None),
        has_attachments: int = Query(0),
        date_from: str | None = Query(None),
        date_to: str | None = Query(None),
        cached_only: int = Query(0),
        cache_bust: str | None = Query(None, alias="_"),
        refresh: int = Query(0),
        owner: str = Depends(_require_owner),
    ):
        calls["list"] = {k: v for k, v in locals().items() if k != "calls"}
        # Same validation as the real handler: a leaked Query(None) is truthy
        # and makes strptime raise TypeError.
        for value in (date_from, date_to):
            if value:
                datetime.strptime(value, "%Y-%m-%d")
        return {"emails": [], "owner": owner}

    @router.get("/read/{uid}")
    async def read_email_by_uid(
        uid: str,
        folder: str = Query("INBOX"),
        account_id: str | None = Query(None),
        mark_seen: bool = Query(True),
        full: bool = Query(False),
        owner: str = Depends(_require_owner),
    ):
        calls["read"] = {k: v for k, v in locals().items() if k != "calls"}
        return {"uid": uid, "owner": owner}

    @router.post("/draft")
    async def save_draft(req: dict, owner: str = Depends(_require_owner)):
        calls["draft"] = {"req": req, "owner": owner}
        return {"ok": True}

    @router.post("/send")
    async def send_email(req: dict, background_tasks: BackgroundTasks, owner: str = Depends(_require_owner)):
        calls["send"] = {"req": req, "background_tasks": background_tasks, "owner": owner}
        return {"ok": True}

    return router


def _document_router(calls: dict) -> APIRouter:
    router = APIRouter()

    @router.get("/api/documents/library")
    async def documents_library(
        request: Request,
        search: Optional[str] = Query(None),
        language: Optional[str] = Query(None),
        sort: str = Query("recent"),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
        archived: bool = Query(False),
    ):
        calls["library"] = {k: v for k, v in locals().items() if k not in ("request", "calls")}
        return {"documents": [], "total": 0}

    return router


@pytest.fixture
def codex_client():
    calls: dict = {}
    app = FastAPI()

    @app.middleware("http")
    async def _fake_bearer(request, call_next):
        # Same state the real AuthMiddleware sets for a valid scoped token.
        request.state.current_user = "api"
        request.state.api_token = True
        request.state.api_token_owner = "alice"
        request.state.api_token_scopes = ["email:read", "email:draft", "email:send", "documents:read"]
        return await call_next(request)

    app.include_router(setup_codex_routes(
        email_router=_email_router(calls),
        document_router=_document_router(calls),
    ))
    return TestClient(app), calls


def test_codex_list_emails_passes_plain_values(codex_client):
    client, calls = codex_client
    resp = client.get("/api/codex/emails?limit=3")
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"] == "alice"
    kwargs = calls["list"]
    _assert_plain(kwargs)
    assert kwargs["limit"] == 3
    assert kwargs["date_from"] is None and kwargs["date_to"] is None
    assert kwargs["cached_only"] == 0 and kwargs["refresh"] == 0
    assert kwargs["cache_bust"] is None


def test_codex_read_email_passes_plain_values(codex_client):
    client, calls = codex_client
    resp = client.get("/api/codex/emails/42")
    assert resp.status_code == 200, resp.text
    kwargs = calls["read"]
    _assert_plain(kwargs)
    assert kwargs["uid"] == "42"
    assert kwargs["owner"] == "alice"
    assert kwargs["mark_seen"] is False
    assert kwargs["full"] is False


def test_codex_email_draft_and_send_pass_owner(codex_client, monkeypatch):
    import routes.email_routes as email_routes

    monkeypatch.setattr(email_routes, "SendEmailRequest", lambda **body: dict(body))
    client, calls = codex_client
    body = {"to": "bob@example.com", "subject": "hi", "body": "x"}
    assert client.post("/api/codex/emails/draft", json=body).status_code == 200
    assert client.post("/api/codex/emails/send", json=body).status_code == 200
    _assert_plain(calls["draft"])
    _assert_plain(calls["send"])
    assert calls["draft"]["owner"] == calls["send"]["owner"] == "alice"


def test_codex_documents_library_passes_plain_values(codex_client):
    client, calls = codex_client
    resp = client.get("/api/codex/documents?limit=5")
    assert resp.status_code == 200, resp.text
    kwargs = calls["library"]
    _assert_plain(kwargs)
    assert kwargs["limit"] == 5 and kwargs["sort"] == "recent"


def test_call_handler_fills_param_defaults_and_rejects_unresolved_depends():
    async def handler(a, b: int = Query(7), c: str | None = Query(None), owner: str = Depends(_require_owner)):
        return a, b, c, owner

    import asyncio

    assert asyncio.run(_call_handler(handler, 1, owner="alice")) == (1, 7, None, "alice")
    with pytest.raises(TypeError, match="owner"):
        asyncio.run(_call_handler(handler, 1))


# ── AuthMiddleware: downstream errors are not auth failures ──────────────

_MIDDLEWARE_PROBE = textwrap.dedent(
    """
    import asyncio
    import json

    import bcrypt
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    import app as app_module

    TOKEN = "ody_" + "A" * 43


    class _AuthManager:
        is_configured = True
        users = {"alice": {}}

        @staticmethod
        def validate_token(_token):
            return False

        @staticmethod
        def get_username_for_token(_token):
            return None


    async def ok(request):
        return JSONResponse({
            "owner": request.state.api_token_owner,
            "scopes": request.state.api_token_scopes,
        })


    routes = Starlette(routes=[Route("/api/ok", ok)])


    async def downstream(scope, receive, send):
        # Raise at the ASGI level, like the inner middleware stack does in
        # production (ServerErrorMiddleware sits *outside* AuthMiddleware).
        if scope["path"] == "/api/boom":
            raise RuntimeError("downstream route failure")
        await routes(scope, receive, send)


    async def _case(path, token):
        manager = _AuthManager()
        app_module.auth_manager = manager
        thash = bcrypt.hashpw(TOKEN.encode(), bcrypt.gensalt(rounds=4)).decode()
        app_module._token_cache = {TOKEN[:8]: [("tok-1", thash, "alice", ["email:read"])]}
        app_module.app.state._token_cache_dirty = False

        middleware = app_module.AuthMiddleware(downstream)
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("192.0.2.10", 4321), "server": ("testserver", 80), "app": routes,
        }
        sent = []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        raised = None
        try:
            await middleware(scope, receive, send)
        except Exception as exc:
            raised = type(exc).__name__
        start = next((m for m in sent if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return {
            "status": start["status"] if start else None,
            "body": body.decode(errors="replace"),
            "raised": raised,
        }


    async def main():
        print("RESULT=" + json.dumps({
            "ok": await _case("/api/ok", TOKEN),
            "boom": await _case("/api/boom", TOKEN),
            "invalid": await _case("/api/ok", "ody_" + "B" * 43),
        }))


    asyncio.run(main())
    """
)


def test_auth_middleware_does_not_mask_route_errors_as_invalid_token(tmp_path):
    env = os.environ.copy()
    env.update({
        "AUTH_ENABLED": "true",
        "CHROMADB_CONNECT_TIMEOUT": "0.01",
        "CHROMADB_HOST": "127.0.0.1",
        "CHROMADB_PORT": "9",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
        "LOCALHOST_BYPASS": "false",
        "ODYSSEUS_DATA_DIR": str(tmp_path),
        "ODYSSEUS_DISABLE_MCP": "1",
        "OPENAI_API_KEY": "",
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
    })
    result = subprocess.run(
        [sys.executable, "-c", _MIDDLEWARE_PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    line = next((l for l in result.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line is not None, result.stdout
    payload = json.loads(line.removeprefix("RESULT="))

    # Valid token reaches the route with owner/scopes attached.
    assert payload["ok"]["status"] == 200
    assert json.loads(payload["ok"]["body"]) == {"owner": "alice", "scopes": ["email:read"]}

    # A route exception after successful auth propagates to the normal error
    # handling (ServerErrorMiddleware -> 500); it is never a 401.
    boom = payload["boom"]
    assert boom["status"] != 401, boom
    assert "Invalid API token" not in boom["body"], boom
    assert boom["raised"] == "RuntimeError", boom

    # Unknown token is still rejected exactly as before.
    assert payload["invalid"]["status"] == 401
    assert json.loads(payload["invalid"]["body"]) == {"error": "Invalid API token"}

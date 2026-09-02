"""Behavior-level coverage for the production API-token middleware boundary."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_production_auth_middleware_enforces_api_token_capabilities(tmp_path):
    """Drive the real ``app.AuthMiddleware`` in an isolated subprocess."""

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
    probe = textwrap.dedent(
        """
        import asyncio
        import json

        from starlette.requests import Request
        from starlette.responses import JSONResponse

        import app as app_module


        RAW_TOKEN = "ody_" + "a" * 43


        class _AuthManager:
            is_configured = True
            users = {"alice": {}}

            @staticmethod
            def validate_token(_token):
                return False

            @staticmethod
            def get_username_for_token(_token):
                return None


        app_module.auth_manager = _AuthManager()
        app_module.app.state.auth_manager = app_module.auth_manager
        app_module.app.state._token_cache_dirty = False
        app_module._bcrypt.checkpw = lambda *_args: True


        def _request(
            method,
            path,
            *,
            loopback,
            authorization=None,
            raw_path=None,
        ):
            return Request({
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": raw_path or path.encode(),
                "root_path": "",
                "query_string": b"",
                "headers": [
                    (
                        b"authorization",
                        (authorization or f"Bearer {RAW_TOKEN}").encode(),
                    ),
                ],
                "client": (
                    "127.0.0.1" if loopback else "192.0.2.10",
                    4321,
                ),
                "server": ("testserver", 80),
                "app": app_module.app,
            })


        async def _case(
            path,
            scopes,
            *,
            loopback=False,
            localhost_bypass=False,
            authorization=None,
            method="GET",
            raw_path=None,
        ):
            app_module.LOCALHOST_BYPASS = localhost_bypass
            app_module._token_cache.clear()
            app_module._token_cache[RAW_TOKEN[:8]] = [
                ("token-1", "unused-hash", "alice", scopes),
            ]
            request = _request(
                method,
                path,
                loopback=loopback,
                authorization=authorization,
                raw_path=raw_path,
            )
            calls = []

            async def call_next(received):
                calls.append(received)
                return JSONResponse({"reached": True})

            middleware = app_module.AuthMiddleware(app_module.app)
            response = await middleware.dispatch(request, call_next)
            return {
                "status": response.status_code,
                "body": json.loads(response.body),
                "called": len(calls),
                "api_token": getattr(request.state, "api_token", None),
                "owner": getattr(request.state, "api_token_owner", None),
            }


        async def main():
            original_create_task = app_module._asyncio.create_task
            app_module._asyncio.create_task = lambda coroutine: coroutine.close()
            try:
                forbidden = await _case("/api/unregistered", ["chat"])
                allowed = await _case("/api/models", ["chat"])
                encoded_calendar_uids = {
                    "question_mark": await _case(
                        "/api/codex/calendar/events/team?primary",
                        ["calendar:write"],
                        method="DELETE",
                        raw_path=(
                            b"/api/codex/calendar/events/team%3Fprimary"
                        ),
                    ),
                    "hash": await _case(
                        "/api/codex/calendar/events/team#primary",
                        ["calendar:write"],
                        method="DELETE",
                        raw_path=(
                            b"/api/codex/calendar/events/team%23primary"
                        ),
                    ),
                }
                cookbook_forbidden = {}
                for label, (method, path) in {
                    "tasks": ("GET", "/api/codex/cookbook/tasks"),
                    "servers": ("GET", "/api/codex/cookbook/servers"),
                    "output": (
                        "GET",
                        "/api/codex/cookbook/output/serve-1",
                    ),
                    "cached": ("GET", "/api/codex/cookbook/cached"),
                    "presets": ("GET", "/api/codex/cookbook/presets"),
                    "serve": ("POST", "/api/codex/cookbook/serve"),
                    "stop": (
                        "POST",
                        "/api/codex/cookbook/stop/serve-1",
                    ),
                    "preset": (
                        "POST",
                        "/api/codex/cookbook/preset/default",
                    ),
                    "adopt": ("POST", "/api/codex/cookbook/adopt"),
                }.items():
                    cookbook_forbidden[label] = await _case(
                        path,
                        ["cookbook:read", "cookbook:launch"],
                        method=method,
                    )
                local_wrong_scope = {}
                for label, authorization in {
                    "canonical": f"Bearer {RAW_TOKEN}",
                    "lowercase": f"bearer {RAW_TOKEN}",
                    "uppercase": f"BEARER {RAW_TOKEN}",
                    "mixed_case": f"bEaReR {RAW_TOKEN}",
                    "multiple_spaces": f"Bearer   {RAW_TOKEN}",
                }.items():
                    local_wrong_scope[label] = await _case(
                        "/api/models",
                        ["todos:read"],
                        loopback=True,
                        localhost_bypass=True,
                        authorization=authorization,
                    )
            finally:
                app_module._asyncio.create_task = original_create_task

            print("RESULT=" + json.dumps({
                "forbidden": forbidden,
                "allowed": allowed,
                "cookbook_forbidden": cookbook_forbidden,
                "encoded_calendar_uids": encoded_calendar_uids,
                "local_wrong_scope": local_wrong_scope,
            }, sort_keys=True))


        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    result_line = next(
        (line for line in result.stdout.splitlines() if line.startswith("RESULT=")),
        None,
    )
    assert result_line is not None, result.stdout
    observed = json.loads(result_line.removeprefix("RESULT="))

    generic_error = {"error": "API token is not authorized for this endpoint"}
    assert observed["forbidden"] == {
        "status": 403,
        "body": generic_error,
        "called": 0,
        "api_token": None,
        "owner": None,
    }
    assert observed["allowed"] == {
        "status": 200,
        "body": {"reached": True},
        "called": 1,
        "api_token": True,
        "owner": "alice",
    }
    assert observed["encoded_calendar_uids"] == {
        "question_mark": observed["allowed"],
        "hash": observed["allowed"],
    }
    expected_forbidden = {
        "status": 403,
        "body": generic_error,
        "called": 0,
        "api_token": None,
        "owner": None,
    }
    assert observed["cookbook_forbidden"] == {
        label: expected_forbidden
        for label in (
            "tasks",
            "servers",
            "output",
            "cached",
            "presets",
            "serve",
            "stop",
            "preset",
            "adopt",
        )
    }
    assert observed["local_wrong_scope"] == {
        "canonical": expected_forbidden,
        "lowercase": expected_forbidden,
        "uppercase": expected_forbidden,
        "mixed_case": expected_forbidden,
        "multiple_spaces": expected_forbidden,
    }

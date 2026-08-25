"""Auth middleware must evaluate the same path that Starlette routes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from urllib.parse import urljoin, urlparse

import pytest

from core.middleware import (
    get_application_route_path,
    path_is_route_or_child,
    with_asgi_root_path,
)


ROOT = Path(__file__).resolve().parents[1]


def test_login_page_uses_mount_aware_urls():
    html = (ROOT / "static/login.html").read_text(encoding="utf-8")

    assert "window.__odysseusLoginAppUrl" in html
    for api_path in (
        "/api/version",
        "/api/auth/policy",
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/setup",
        "/api/auth/signup",
        "/api/sessions",
        "/api/auth/features",
        "/api/auth/settings",
    ):
        assert f"appUrl('{api_path}')" in html

    assert "window.location.replace(appUrl('/'))" in html
    assert (
        "__odysseusLoginAppUrl || ((path) => path))('/static/js/theme.js')"
        in html
    )


def test_login_page_static_assets_resolve_under_mount_path():
    html = (ROOT / "static/login.html").read_text(encoding="utf-8")
    for forbidden in (
        "fetch('/api/",
        'fetch("/api/',
        "window.location.replace('/')",
        'window.location.replace("/")',
        "import('/static/",
        'href="/static/',
        "url('/static/",
    ):
        assert forbidden not in html

    mounted_login_url = "https://example.test/odysseus/login"
    for relative_asset, mounted_path in (
        ("static/manifest.json", "/odysseus/static/manifest.json"),
        ("static/icons/icon-192.png", "/odysseus/static/icons/icon-192.png"),
        (
            "static/fonts/FiraCode-Regular.woff2",
            "/odysseus/static/fonts/FiraCode-Regular.woff2",
        ),
        (
            "static/fonts/FiraCode-SemiBold.woff2",
            "/odysseus/static/fonts/FiraCode-SemiBold.woff2",
        ),
    ):
        assert relative_asset in html
        assert urlparse(urljoin(mounted_login_url, relative_asset)).path == mounted_path


@pytest.mark.parametrize(
    ("root_path", "path", "expected"),
    [
        ("", "/api/models", "/api/models"),
        ("/odysseus", "/odysseus/api/models", "/api/models"),
        ("/odysseus/", "/odysseus//api/models", "/api/models"),
        ("/", "//api/models", "/api/models"),
        ("/odysseus", "/odyssey/api/models", "/odyssey/api/models"),
        ("/app", "/application/api/models", "/application/api/models"),
        ("/odysseus", "/odysseus", ""),
    ],
)
def test_application_route_path_matches_starlette_semantics(
    root_path,
    path,
    expected,
):
    assert get_application_route_path({
        "root_path": root_path,
        "path": path,
    }) == expected


@pytest.mark.parametrize(
    ("root_path", "expected"),
    [
        ("", "/login"),
        ("/odysseus", "/odysseus/login"),
        ("/odysseus/", "/odysseus/login"),
        ("/", "/login"),
    ],
)
def test_client_redirect_path_includes_asgi_root_path(root_path, expected):
    assert with_asgi_root_path({"root_path": root_path}, "/login") == expected


def test_route_prefix_matching_is_segment_aware():
    assert path_is_route_or_child("/assets", "/assets") is True
    assert path_is_route_or_child("/assets/app.js", "/assets") is True
    assert path_is_route_or_child("/assets-v2/app.js", "/assets") is False


def test_real_auth_middleware_uses_application_relative_path(tmp_path):
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

        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        import app as app_module


        class _AuthManager:
            def __init__(self, configured):
                self.is_configured = configured

            @staticmethod
            def validate_token(_token):
                return False

            @staticmethod
            def get_username_for_token(_token):
                return None


        def _scope(root_path, route_path, downstream):
            full_path = root_path + route_path
            return {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": full_path,
                "raw_path": full_path.encode(),
                "root_path": root_path,
                "query_string": b"",
                "headers": [],
                "client": ("192.0.2.10", 4321),
                "server": ("testserver", 80),
                "app": downstream,
            }


        async def _case(root_path, route_path, *, configured):
            manager = _AuthManager(configured)
            app_module.auth_manager = manager
            calls = []

            async def endpoint(request):
                calls.append(request)
                return JSONResponse({"reached": True})

            downstream = Starlette(routes=[Route(route_path, endpoint)])
            downstream.state.auth_manager = manager
            middleware = app_module.AuthMiddleware(downstream)
            scope = _scope(root_path, route_path, downstream)
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

            await middleware(scope, receive, send)
            response_start = next(m for m in sent if m["type"] == "http.response.start")
            headers = {k.decode().lower(): v.decode() for k, v in response_start["headers"]}
            return {
                "status": response_start["status"],
                "location": headers.get("location"),
                "called": len(calls),
            }


        async def main():
            setup = await _case("/odysseus", "/api/auth/setup", configured=False)
            mounted_api = await _case("/odysseus", "/api/models", configured=True)
            mounted_browser = await _case("/odysseus", "/notes", configured=True)
            webhook = await _case(
                "/odysseus",
                "/api/tasks/task-1/webhook/secret-token",
                configured=True,
            )
            static_child = await _case(
                "/odysseus",
                "/static/app.js",
                configured=True,
            )
            static_lookalike = await _case(
                "/odysseus",
                "/static-v2/app.js",
                configured=True,
            )
            default_api = await _case("", "/api/models", configured=True)
            print("RESULT=" + json.dumps({
                "setup": setup,
                "mounted_api": mounted_api,
                "mounted_browser": mounted_browser,
                "webhook": webhook,
                "static_child": static_child,
                "static_lookalike": static_lookalike,
                "default_api": default_api,
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
    payload = json.loads(result_line.removeprefix("RESULT="))

    assert payload["setup"] == {"status": 200, "location": None, "called": 1}
    assert payload["webhook"] == {"status": 200, "location": None, "called": 1}
    assert payload["static_child"] == {
        "status": 200,
        "location": None,
        "called": 1,
    }
    assert payload["static_lookalike"] == {
        "status": 302,
        "location": "/odysseus/login",
        "called": 0,
    }
    assert payload["mounted_browser"] == {
        "status": 302,
        "location": "/odysseus/login",
        "called": 0,
    }
    for name in ("mounted_api", "default_api"):
        assert payload[name] == {"status": 401, "location": None, "called": 0}

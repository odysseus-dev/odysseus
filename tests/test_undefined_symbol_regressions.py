import asyncio
import importlib
from pathlib import Path

import httpx

import mcp_servers.email_server as email_server
import routes.cookbook_routes as cookbook_routes
import src.constants as constants


def _hf_gguf_endpoint():
    for route in cookbook_routes.setup_cookbook_routes().routes:
        if route.path == "/api/cookbook/hf-gguf-files":
            return route.endpoint
    raise AssertionError("GET /api/cookbook/hf-gguf-files route not found")


def test_hf_gguf_error_path_returns_error_instead_of_raising_name_error(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            raise RuntimeError("huggingface unavailable")

    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)

    result = asyncio.run(_hf_gguf_endpoint()(repo_id="org/model", owner="alice"))

    assert result == {"ok": False, "files": [], "error": "HF API request failed"}


def test_email_server_fallback_uses_data_dir_when_scheduled_constant_missing():
    original = constants.SCHEDULED_EMAILS_DB
    del constants.SCHEDULED_EMAILS_DB
    email_server.__dict__.pop("DATA_DIR", None)
    try:
        reloaded = importlib.reload(email_server)
        assert reloaded.SCHEDULED_EMAILS_DB == str(
            Path(constants.DATA_DIR) / "scheduled_emails.db"
        )
    finally:
        constants.SCHEDULED_EMAILS_DB = original
        importlib.reload(email_server)

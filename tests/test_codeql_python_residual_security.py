"""Adversarial tests for the residual Python CodeQL fixes (PR #6503 lane).

Covers py/reflective-xss (search page), py/path-injection (context_info host
path oracle) and py/stack-trace-exposure (readiness, stream errors, task
drafting).
"""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest


# -- py/reflective-xss: GET /search/web?q= ----------------------------------

_XSS_QUERIES = [
    '</script><script src="https://cdn.jsdelivr.net/gh/evil/x@1/p.js"></script>',
    "</SCRIPT><img src=x onerror=alert(1)>",
    "<!--<script>",
    "a & b < c > d   \"quoted\" 'single'",
]


def _search_page(q):
    from routes.search.search_routes import setup_search_routes

    router = setup_search_routes(SimpleNamespace())
    endpoint = next(r.endpoint for r in router.routes if r.path == "/search/web")
    request = SimpleNamespace(state=SimpleNamespace(csp_nonce="n0nce"))
    return asyncio.run(endpoint(request=request, q=q)).body.decode("utf-8")


@pytest.mark.parametrize("q", _XSS_QUERIES)
def test_search_page_query_cannot_break_out_of_inline_script(q):
    page = _search_page(q)
    script = page.split('<script nonce="n0nce">', 1)[1]
    # The page's own closing tag is the only one; nothing from q survives raw.
    assert script.lower().count("</script") == 1
    assert "<!--" not in script
    literal = re.search(r"const initialQuery = (.*?);\n", script).group(1)
    assert "<" not in literal and ">" not in literal and "&" not in literal
    # The JS string value is unchanged for the search itself.
    assert json.loads(literal) == q.strip()


# -- py/path-injection: /session/{id}/context_info?cwd= ----------------------

def _context_info(monkeypatch, *, admin, cwd):
    import routes.session_routes as routes
    from fastapi import APIRouter

    monkeypatch.setattr(routes, "_verify_session_owner", lambda *args: None)
    monkeypatch.setattr("src.tool_security.blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr("src.tool_security.owner_is_admin_or_single_user", lambda owner: admin)
    monkeypatch.setattr(routes, "router", APIRouter(prefix="/api"))
    session = SimpleNamespace(endpoint_url="", model="", cwd="")
    router = routes.setup_session_routes(SimpleNamespace(get_session=lambda sid: session), {})
    endpoint = next(
        r.endpoint for r in router.routes if r.path == "/api/session/{session_id}/context_info"
    )
    request = SimpleNamespace(state=SimpleNamespace(
        api_token=False, api_token_owner=None, api_token_scopes=[], current_user="someone",
    ))
    return asyncio.run(endpoint(request, "session-1", cwd=cwd))


def test_context_info_hides_host_paths_from_non_admin(monkeypatch, tmp_path):
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    result = _context_info(monkeypatch, admin=False, cwd=str(tmp_path))
    assert result["agents_md"] == []
    assert result["workspace"]["exists_in_backend"] is False


def test_context_info_still_reports_host_paths_for_admin(monkeypatch, tmp_path):
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    result = _context_info(monkeypatch, admin=True, cwd=str(tmp_path))
    assert {"path": str(tmp_path / "AGENTS.md"), "source": "workspace"} in result["agents_md"]
    assert result["workspace"]["exists_in_backend"] is True


# -- py/stack-trace-exposure ---------------------------------------------------

_SECRET = "postgresql://odysseus:hunter2@10.0.0.5/prod /srv/odysseus/data"


def test_readiness_does_not_return_raw_database_error(monkeypatch):
    import core.database as database
    from src.readiness import check_readiness

    class _Engine:
        def connect(self):
            raise RuntimeError(_SECRET)

    monkeypatch.setattr(database, "engine", _Engine())
    result = check_readiness()
    db_check = result["checks"]["database"]
    assert db_check == {"ok": False, "error_type": "RuntimeError"}
    assert "hunter2" not in json.dumps(result)
    assert result["ready"] is False


def test_readiness_does_not_return_raw_data_dir_error(monkeypatch, tmp_path):
    import core.constants as constants
    from src.readiness import check_readiness

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not a directory", encoding="utf-8")
    monkeypatch.setattr(constants, "DATA_DIR", str(blocker / "data"))
    result = check_readiness()
    data_check = result["checks"]["data_dir"]
    assert data_check["ok"] is False
    assert set(data_check) == {"ok", "error_type"}
    assert str(tmp_path) not in json.dumps(result)


def test_stream_failure_message_is_generic():
    from src.llm_core import _stream_failure_message

    assert _stream_failure_message(RuntimeError(_SECRET)) == "Model stream failed (RuntimeError)"


def test_catch_all_stream_error_events_do_not_embed_exception_text():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/llm_core.py").read_text(encoding="utf-8")
    assert '{"error": str(e), "status": 502' not in source


def _parse_task(monkeypatch, exc):
    from fastapi import HTTPException  # noqa: F401  (raised by callers below)
    from unittest.mock import MagicMock
    import routes.task.task_routes as task_routes
    import src.endpoint_resolver as resolver
    import src.llm_core as llm_core

    async def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(resolver, "resolve_endpoint", lambda *a, **k: ("http://llm", "m", {}))
    monkeypatch.setattr(llm_core, "llm_call_async", _boom)
    router = task_routes.setup_task_routes(MagicMock())
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/tasks/parse")

    class _Request:
        state = SimpleNamespace(current_user="alice")

        async def json(self):
            return {"description": "every day at 9 summarize the news"}

    return asyncio.run(endpoint(_Request()))


def test_parse_task_hides_unexpected_exception_text(monkeypatch):
    result = _parse_task(monkeypatch, RuntimeError(_SECRET))
    assert result == {"success": False, "message": "Could not draft a task (RuntimeError)"}


def test_parse_task_keeps_curated_upstream_http_error(monkeypatch):
    from fastapi import HTTPException

    result = _parse_task(monkeypatch, HTTPException(401, "Upstream rejected the API key"))
    assert result == {"success": False, "message": "Upstream rejected the API key"}

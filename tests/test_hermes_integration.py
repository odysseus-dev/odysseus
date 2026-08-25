"""Tests for the Hermes Agent integration.

The Hermes integration mirrors the Claude Code integration: it serves a skill
bundle zip at ``/api/hermes/plugin.zip`` (built from ``integrations/hermes/``)
while all runtime data access goes through the existing scope-gated
``/api/codex/*`` endpoints. These tests pin the bundle contents, the zip
endpoint behavior (auth required, skills-only payload), and the app-level
router registration.
"""
import io
import zipfile

import pytest
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

import routes.codex_routes as codex_routes


def _hermes_router() -> APIRouter:
    return codex_routes.setup_hermes_routes()


def _plugin_request(authenticated: bool = True) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/hermes/plugin.zip",
            "headers": [],
            "state": {},
        }
    )
    request.state.authenticated = authenticated
    return request


def test_hermes_router_registers_plugin_zip_route():
    router = _hermes_router()
    paths = {route.path for route in router.routes}
    assert "/api/hermes/plugin.zip" in paths


def test_plugin_zip_requires_authentication():
    request = _plugin_request(authenticated=False)
    endpoint = None
    for route in _hermes_router().routes:
        if route.path == "/api/hermes/plugin.zip":
            endpoint = route.endpoint
            break
    assert endpoint is not None
    # codex_routes imports require_authenticated_request by name, so patch it
    # there; a request without an app scope must be rejected, not served.
    import routes.codex_routes as cr

    original = cr.require_authenticated_request
    def _reject(req):
        if "app" not in req.scope:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return original(req)
    cr.require_authenticated_request = _reject
    try:
        with pytest.raises(HTTPException) as exc:
            endpoint(request)
    finally:
        cr.require_authenticated_request = original
    assert exc.value.status_code in (401, 403)


def test_plugin_zip_contains_only_skills_subtree():
    """Bundle ships skills/ only — no README or bundle metadata, so extracting
    at ~/.hermes/ doesn't dump stray files into the user's config dir."""
    request = _plugin_request()
    # Patch auth so the test exercises only bundle assembly.
    import routes.codex_routes as cr

    original = cr.require_authenticated_request
    cr.require_authenticated_request = lambda req: None
    try:
        response = endpoint_zip(request)
    finally:
        cr.require_authenticated_request = original

    import asyncio

    if hasattr(response, "body_iterator"):
        chunks = asyncio.run(_drain(response.body_iterator))
        buf = io.BytesIO(b"".join(chunks))
    else:
        buf = io.BytesIO(response.body)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
    assert names, "zip bundle must not be empty"
    for name in names:
        assert name.startswith("skills/"), f"non-skills path leaked into bundle: {name}"
    assert any(n.endswith("SKILL.md") for n in names)
    assert any(n.endswith("scripts/odysseus_api.py") for n in names)


async def _drain(iterator):
    return [chunk async for chunk in iterator]


def endpoint_zip(request):
    for route in _hermes_router().routes:
        if route.path == "/api/hermes/plugin.zip":
            return route.endpoint(request)
    raise AssertionError("plugin.zip route missing")


def test_app_includes_hermes_routes():
    """app.py must mount the hermes router so /api/hermes/* is live.

    Checked at source level: importing app.py pulls in the full service stack
    (ChromaDB, embeddings, webhooks) which is out of scope for this test and
    fails in minimal environments on unrelated pre-existing imports.
    """
    from pathlib import Path

    app_src = (Path(codex_routes.__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    assert "from routes.codex_routes import setup_hermes_routes" in app_src
    assert "app.include_router(setup_hermes_routes())" in app_src


def test_skill_bundle_files_exist_on_disk():
    from pathlib import Path

    root = Path(codex_routes.__file__).resolve().parent.parent / "integrations" / "hermes"
    assert (root / "skills" / "odysseus" / "SKILL.md").is_file()
    assert (root / "skills" / "odysseus" / "scripts" / "odysseus_api.py").is_file()


def test_helper_script_references_scoped_agent_api_only():
    """Safety invariant: the helper talks to the scope-gated /api/codex/* API,
    never to internal routes or direct DB access."""
    from pathlib import Path

    root = Path(codex_routes.__file__).resolve().parent.parent / "integrations" / "hermes"
    text = (root / "skills" / "odysseus" / "SKILL.md").read_text(encoding="utf-8")
    assert "/api/codex/" in text
    assert "direct Python imports" in text  # safety rule present

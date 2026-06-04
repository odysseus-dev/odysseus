"""Regression tests for endpoint owner scoping in secondary model routes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_compare_start_rejects_raw_endpoint_for_non_admin():
    from routes.compare_routes import setup_compare_routes

    router = setup_compare_routes(SimpleNamespace(create_session=lambda **_: None, sessions={}))
    start = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/compare/start"
    )
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(is_admin=lambda user: False)
            )
        ),
    )

    with pytest.raises(HTTPException) as exc:
        start(
            request,
            prompt="p",
            model_a="a",
            model_b="b",
            endpoint_a="http://127.0.0.1:8000/v1",
            endpoint_b="http://127.0.0.1:8001/v1",
        )

    assert exc.value.status_code == 403


def test_compare_endpoint_key_lookup_is_owner_scoped():
    body = Path("routes/compare_routes.py").read_text(encoding="utf-8")
    start_body = body.split("def start_comparison", 1)[1].split("# Store comparison record", 1)[0]
    helper_body = body.split("def _owned_endpoint_by_url", 1)[1].split("class RecordVoteRequest", 1)[0]

    assert "_reject_raw_endpoint_url_for_non_admin" in start_body
    assert "_owned_endpoint_by_url(db, base, user)" in start_body
    assert "owner_filter(q, ModelEndpoint, owner)" in helper_body


def test_gallery_image_endpoint_lookups_are_owner_scoped():
    body = Path("routes/gallery_routes.py").read_text(encoding="utf-8")
    helper_body = body.split("def _visible_image_endpoint_query", 1)[1].split(
        "def _first_visible_image_endpoint", 1
    )[0]

    assert "owner_filter(q, ModelEndpoint, owner)" in helper_body
    assert body.count("_first_visible_image_endpoint(db, user)") >= 4
    assert body.count("_visible_image_endpoint_for_base(db,") >= 2
    assert "def _current_user_is_admin" in body
    assert body.count('raise HTTPException(403, "Choose a registered image endpoint")') == 2
    for marker in (
        "async def gallery_ai_upscale",
        "async def gallery_style_transfer",
        "async def inpaint_proxy",
        "async def harmonize_image",
    ):
        section = body.split(marker, 1)[1].split("@router.", 1)[0]
        assert "user = require_privilege(request, \"can_generate_images\")" in section
        assert (
            "_first_visible_image_endpoint(db, user)" in section
            or "_visible_image_endpoint_for_base(db," in section
        )


def test_research_endpoint_resolution_passes_owner():
    body = Path("routes/research_routes.py").read_text(encoding="utf-8")

    assert "def _resolve_research_endpoint(sess, owner:" in body
    assert 'resolve_endpoint("research", owner=user)' in body
    assert 'resolve_endpoint("utility", owner=user)' in body
    assert 'resolve_endpoint("default", owner=user)' in body
    assert 'resolve_endpoint("chat", owner=user)' in body
    helper_body = body.split("def _owned_enabled_endpoint", 1)[1].split("def setup_research_routes", 1)[0]
    assert "owner_filter(q, ModelEndpoint, owner)" in helper_body
    assert body.count("_owned_enabled_endpoint(db, user") >= 2

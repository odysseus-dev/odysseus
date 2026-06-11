"""Probing a model_type="image" endpoint must return image models.

_probe_endpoint always filtered the provider's model list through
_is_chat_model, and gpt-image-*/dall-e-*/sora are explicitly NON_CHAT, so an
endpoint added with type IMAGE could only ever list chat models (issue #3920:
"always loads llm models"). The probe now takes the endpoint's model_type and
keeps the matching modality; image endpoints with unrecognized local
diffusion names fall back to the unfiltered list instead of an empty one.

Follows the test_endpoint_probing.py import/mocking pattern: real
routes.model_routes helpers, httpx faked, no network.
"""
import sys
import types
from unittest.mock import MagicMock

import httpx

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules, preserve_import_state

with preserve_import_state("core.database", "src.database", "core.session_manager", "routes.model_routes"):
    clear_fake_endpoint_resolver_modules()

    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
            "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        _core_db.utcnow_naive = MagicMock()
        sys.modules["core.database"] = _core_db

    import routes.model_routes as model_routes
    import src.endpoint_resolver as endpoint_resolver
    from routes.model_routes import (
        _filter_models_for_type,
        _is_image_model,
        _probe_endpoint,
    )

OPENAI_STYLE_LIST = {
    "data": [
        {"id": "gpt-4o"},
        {"id": "gpt-4o-mini"},
        {"id": "dall-e-3"},
        {"id": "gpt-image-1"},
        {"id": "whisper-1"},
    ]
}


def _patch_resolve(monkeypatch):
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))


def _resp(status, *, json=None, url="https://api.example.com/v1/models"):
    req = httpx.Request("GET", url)
    kwargs = {"request": req}
    if json is not None:
        kwargs["json"] = json
    return httpx.Response(status, **kwargs)


def test_is_image_model_recognizes_common_ids():
    for mid in ("gpt-image-1", "dall-e-3", "FLUX.1-dev", "stable-diffusion-xl", "sd3-medium"):
        assert _is_image_model(mid), mid
    for mid in ("gpt-4o", "llama3:8b", "whisper-1", "text-embedding-3-small"):
        assert not _is_image_model(mid), mid


def test_image_probe_returns_image_models(monkeypatch):
    _patch_resolve(monkeypatch)
    monkeypatch.setattr(
        model_routes.httpx, "get",
        lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(200, json=OPENAI_STYLE_LIST),
    )
    assert _probe_endpoint("https://api.example.com/v1", "key", model_type="image") == [
        "dall-e-3", "gpt-image-1",
    ]


def test_llm_probe_keeps_historical_chat_filter(monkeypatch):
    _patch_resolve(monkeypatch)
    monkeypatch.setattr(
        model_routes.httpx, "get",
        lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(200, json=OPENAI_STYLE_LIST),
    )
    assert _probe_endpoint("https://api.example.com/v1", "key") == ["gpt-4o", "gpt-4o-mini"]


def test_image_filter_falls_back_to_full_list_for_unrecognized_names():
    # Local diffusion servers often expose arbitrary model names; an image
    # endpoint must not end up with an empty picker because of the heuristic.
    models = ["juggernaut-xl-v9", "my-custom-model"]
    assert _filter_models_for_type(models, "image") == models


def test_image_filter_is_case_and_whitespace_tolerant():
    assert _filter_models_for_type(["DALL-E-3", "gpt-4o"], " IMAGE ") == ["DALL-E-3"]

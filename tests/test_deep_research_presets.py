"""Unit tests for Deep Research hardware presets logic."""

import asyncio
import inspect
from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException

from routes.research_routes import setup_research_routes


class SimpleNamespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _request(user: str):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None))
    )


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


@pytest.fixture
def mock_research_handler():
    handler = MagicMock()
    handler._active_tasks = {}
    return handler


@pytest.mark.asyncio
async def test_preset_auto_detection_small(mock_research_handler, monkeypatch):
    # Mock detect_system to return small profile
    mock_detect = MagicMock(return_value={
        "total_ram_gb": 8.0,
        "has_gpu": False,
        "gpu_vram_gb": 0.0
    })
    monkeypatch.setattr("services.hwfit.hardware.detect_system", mock_detect)

    # Mock resolve_endpoint to avoid DB lookups
    monkeypatch.setattr(
        "routes.research_routes.resolve_endpoint",
        MagicMock(return_value=("http://local.test", "model-name", {}))
    )
    # Mock require_privilege to return user
    monkeypatch.setattr(
        "src.auth_helpers.require_privilege",
        MagicMock(return_value="alice")
    )

    router = setup_research_routes(mock_research_handler)
    target = _route(router, "/api/research/start", "POST")

    sig = inspect.signature(target)
    ResearchStartRequest = sig.parameters['body'].annotation

    body = ResearchStartRequest(
        query="test small preset",
        max_rounds=0,
        hardware_preset="auto"
    )

    await target(body=body, request=_request("alice"))

    # Assert correct defaults for small setup
    mock_research_handler.start_research.assert_called_once()
    kwargs = mock_research_handler.start_research.call_args[1]
    assert kwargs["max_rounds"] == 3
    assert kwargs["extraction_concurrency"] == 1
    assert kwargs["extraction_timeout"] == 60


@pytest.mark.asyncio
async def test_preset_auto_detection_medium(mock_research_handler, monkeypatch):
    mock_detect = MagicMock(return_value={
        "total_ram_gb": 16.0,
        "has_gpu": True,
        "gpu_vram_gb": 8.0,
        "gpu_count": 1
    })
    monkeypatch.setattr("services.hwfit.hardware.detect_system", mock_detect)

    monkeypatch.setattr(
        "routes.research_routes.resolve_endpoint",
        MagicMock(return_value=("http://local.test", "model-name", {}))
    )
    monkeypatch.setattr(
        "src.auth_helpers.require_privilege",
        MagicMock(return_value="alice")
    )

    router = setup_research_routes(mock_research_handler)
    target = _route(router, "/api/research/start", "POST")

    sig = inspect.signature(target)
    ResearchStartRequest = sig.parameters['body'].annotation

    body = ResearchStartRequest(
        query="test medium preset",
        max_rounds=0,
        hardware_preset="auto"
    )

    await target(body=body, request=_request("alice"))

    mock_research_handler.start_research.assert_called_once()
    kwargs = mock_research_handler.start_research.call_args[1]
    assert kwargs["max_rounds"] == 5
    assert kwargs["extraction_concurrency"] == 3
    assert kwargs["extraction_timeout"] == 90


@pytest.mark.asyncio
async def test_preset_auto_detection_large(mock_research_handler, monkeypatch):
    mock_detect = MagicMock(return_value={
        "total_ram_gb": 32.0,
        "has_gpu": True,
        "gpu_vram_gb": 24.0,
        "gpu_count": 1
    })
    monkeypatch.setattr("services.hwfit.hardware.detect_system", mock_detect)

    monkeypatch.setattr(
        "routes.research_routes.resolve_endpoint",
        MagicMock(return_value=("http://local.test", "model-name", {}))
    )
    monkeypatch.setattr(
        "src.auth_helpers.require_privilege",
        MagicMock(return_value="alice")
    )

    router = setup_research_routes(mock_research_handler)
    target = _route(router, "/api/research/start", "POST")

    sig = inspect.signature(target)
    ResearchStartRequest = sig.parameters['body'].annotation

    body = ResearchStartRequest(
        query="test large preset",
        max_rounds=0,
        hardware_preset="auto"
    )

    await target(body=body, request=_request("alice"))

    mock_research_handler.start_research.assert_called_once()
    kwargs = mock_research_handler.start_research.call_args[1]
    assert kwargs["max_rounds"] == 8
    assert kwargs["extraction_concurrency"] == 4
    assert kwargs["extraction_timeout"] == 120


@pytest.mark.asyncio
async def test_preset_explicit_override(mock_research_handler, monkeypatch):
    monkeypatch.setattr(
        "routes.research_routes.resolve_endpoint",
        MagicMock(return_value=("http://local.test", "model-name", {}))
    )
    monkeypatch.setattr(
        "src.auth_helpers.require_privilege",
        MagicMock(return_value="alice")
    )

    router = setup_research_routes(mock_research_handler)
    target = _route(router, "/api/research/start", "POST")

    sig = inspect.signature(target)
    ResearchStartRequest = sig.parameters['body'].annotation

    # Pass preset = large but explicitly override parameters
    body = ResearchStartRequest(
        query="test explicit override",
        max_rounds=6,
        hardware_preset="large",
        extraction_concurrency=2,
        extraction_timeout=150
    )

    await target(body=body, request=_request("alice"))

    mock_research_handler.start_research.assert_called_once()
    kwargs = mock_research_handler.start_research.call_args[1]
    # Explicit overrides must take precedence
    assert kwargs["max_rounds"] == 6
    assert kwargs["extraction_concurrency"] == 2
    assert kwargs["extraction_timeout"] == 150

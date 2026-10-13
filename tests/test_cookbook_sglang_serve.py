"""Tests for SGLang model serving in the Cookbook.

Validates SGLang serving on Linux, Windows/WSL, macOS, and Docker (NVIDIA/AMD).
"""
import pytest
from unittest.mock import AsyncMock, patch
from src.tools.cookbook import (
    _cookbook_default_launch_cmd,
    _cookbook_engine_from_model_info,
    _MODEL_PROCESS_PATTERNS,
)


@pytest.mark.parametrize(
    "repo_id, engine, expected_cmd",
    [
        (
            "lmsys/vicuna-7b-v1.3",
            "sglang",
            "python3 -m sglang.launch_server --model-path lmsys/vicuna-7b-v1.3 --host 0.0.0.0 --port 8000",
        ),
        (
            "mistralai/Mistral-7B-v0.1",
            "sgl",
            "python3 -m sglang.launch_server --model-path mistralai/Mistral-7B-v0.1 --host 0.0.0.0 --port 8000",
        ),
    ],
)
def test_cookbook_default_launch_cmd_sglang(repo_id, engine, expected_cmd):
    """Test that SGLang launch commands are constructed correctly."""
    cmd = _cookbook_default_launch_cmd(repo_id, engine, port=8000)
    assert cmd == expected_cmd


@pytest.mark.parametrize(
    "repo_id, tags, expected_engine",
    [
        ("sglang-community/test-model", [], "sglang"),
        ("test-model", ["sglang"], "sglang"),
        ("test-model", [], "vllm"),  # Default fallback
    ],
)
def test_cookbook_engine_from_model_info_sglang(repo_id, tags, expected_engine):
    """Test that SGLang is correctly inferred from model info."""
    info = {"tags": tags}
    engine = _cookbook_engine_from_model_info(repo_id, info)
    assert engine == expected_engine


@pytest.mark.parametrize(
    "cmdline, expected_label",
    [
        ("python3 -m sglang.launch_server --model-path test-model", "SGLang"),
        ("python3 /path/to/sglang/launch_server --model-path test-model", "SGLang"),
    ],
)
def test_model_process_patterns_sglang(cmdline, expected_label):
    """Test that SGLang processes are detected correctly."""
    for label, patterns in _MODEL_PROCESS_PATTERNS:
        for pattern in patterns:
            if pattern.lower() in cmdline.lower():
                assert label == expected_label
                return
    pytest.fail(f"No pattern matched for SGLang command: {cmdline}")


@pytest.mark.asyncio
async def test_serve_sglang_model_linux():
    """Test serving an SGLang model on Linux."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "sglang", "host": ""}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]


@pytest.mark.asyncio
async def test_serve_sglang_model_windows_wsl():
    """Test serving an SGLang model on Windows/WSL."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "sglang", "host": "wsl"}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]


@pytest.mark.asyncio
async def test_serve_sglang_model_macos():
    """Test serving an SGLang model on macOS."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "sglang", "host": "macos"}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]


@pytest.mark.asyncio
async def test_serve_sglang_model_docker_nvidia():
    """Test serving an SGLang model in Docker with NVIDIA GPU support."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "sglang", "host": "docker-nvidia"}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]


@pytest.mark.asyncio
async def test_serve_sglang_model_docker_amd():
    """Test serving an SGLang model in Docker with AMD GPU support."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "sglang", "host": "docker-amd"}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]
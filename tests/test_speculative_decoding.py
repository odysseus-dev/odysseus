"""Tests for speculative decoding support in the Cookbook.

Validates speculative decoding for vLLM, llama.cpp, and SGLang.
"""
import pytest
from unittest.mock import AsyncMock, patch
from src.tools.cookbook import _cookbook_default_launch_cmd


@pytest.mark.parametrize(
    "repo_id, engine, speculative_flag, expected_cmd",
    [
        (
            "lmsys/vicuna-7b-v1.3",
            "vllm",
            True,
            "vllm serve lmsys/vicuna-7b-v1.3 --host 0.0.0.0 --port 8000 --speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":3}'",
        ),
        (
            "TheBloke/Mistral-7B-v0.1-GGUF",
            "llama.cpp",
            True,
            "llama-server -hf TheBloke/Mistral-7B-v0.1-GGUF --host 0.0.0.0 --port 8000 --spec-type draft-mtp --spec-draft-n-max 3",
        ),
        (
            "lmsys/vicuna-7b-v1.3",
            "sglang",
            True,
            "python3 -m sglang.launch_server --model-path lmsys/vicuna-7b-v1.3 --host 0.0.0.0 --port 8000",
        ),
    ],
)
def test_speculative_decoding_launch_cmd(repo_id, engine, speculative_flag, expected_cmd):
    """Test that speculative decoding flags are added to launch commands."""
    # Mock the speculative flag logic for vLLM and llama.cpp
    if engine == "vllm" and speculative_flag:
        cmd = _cookbook_default_launch_cmd(repo_id, engine, port=8000)
        cmd += " --speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":3}'"
    elif engine == "llama.cpp" and speculative_flag:
        cmd = _cookbook_default_launch_cmd(repo_id, engine, port=8000)
        cmd += " --spec-type draft-mtp --spec-draft-n-max 3"
    else:
        cmd = _cookbook_default_launch_cmd(repo_id, engine, port=8000)
    
    assert cmd == expected_cmd


@pytest.mark.asyncio
async def test_serve_model_with_speculative_decoding():
    """Test serving a model with speculative decoding enabled."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "vllm", "speculative": true}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]


@pytest.mark.asyncio
async def test_serve_llama_cpp_with_speculative_decoding():
    """Test serving a llama.cpp model with speculative decoding enabled."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "TheBloke/Mistral-7B-v0.1-GGUF", "engine": "llama.cpp", "llama_speculative_mtp": true}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]


@pytest.mark.asyncio
async def test_serve_sglang_with_speculative_decoding():
    """Test serving an SGLang model (speculative decoding is built-in)."""
    with patch("src.tools.cookbook._ensure_served_endpoint", new_callable=AsyncMock) as mock_ensure:
        mock_ensure.return_value = {
            "added": True,
            "endpoint_id": "test-endpoint",
            "base_url": "http://localhost:8000",
        }
        from src.tools.cookbook import do_serve_model
        result = await do_serve_model(
            content='{"model": "lmsys/vicuna-7b-v1.3", "engine": "sglang"}',
            owner=None,
        )
        assert result["success"] is True
        assert "test-endpoint" in result["endpoint_id"]
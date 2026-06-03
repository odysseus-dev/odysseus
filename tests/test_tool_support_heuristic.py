"""Regression tests for the tool-support heuristic in stream_agent_loop.

Verifies two critical cases:
  1. Ollama's local OpenAI-compatible `/v1` endpoint must NOT enable native
     tool schemas by default. Some models return a single tool-call token and
     no prose when schemas are sent.
  2. api.deepseek.com must still be treated as tool-capable via the host
     allow-list (_API_HOSTS), so cloud deepseek users keep working.
"""
from src.agent_loop import (
    _API_HOSTS,
    _agent_uses_native_tool_schemas,
    _is_ollama_openai_compat_url,
)


def _compute_is_api_model(model: str, endpoint_url: str, endpoint_supports=None) -> bool:
    """Wrap the production heuristic used by stream_agent_loop."""
    return _agent_uses_native_tool_schemas(model, endpoint_url, endpoint_supports)


class TestLocalOllamaToolSupport:
    # --- local Ollama cases (must NOT get tool schemas) ---

    def test_deepseek_r1_7b_local_ollama_no_tools(self):
        result = _compute_is_api_model(
            "deepseek-r1:7b", "http://localhost:11434/v1"
        )
        assert result is False, (
            "deepseek-r1:7b on Ollama must not enable tool schemas "
            "(Ollama returns HTTP 400 for this model)"
        )

    def test_deepseek_r1_14b_local_no_tools(self):
        assert _compute_is_api_model("deepseek-r1:14b", "http://localhost:11434/v1") is False

    def test_deepseek_r1_70b_local_no_tools(self):
        assert _compute_is_api_model("deepseek-r1:70b", "http://127.0.0.1:11434/v1") is False

    def test_deepseek_r1_via_docker_no_tools(self):
        assert _compute_is_api_model(
            "deepseek-r1:7b", "http://host.docker.internal:11434/v1"
        ) is False

    def test_gemma4_local_ollama_v1_no_tools(self):
        assert _compute_is_api_model("gemma4:e2b", "http://localhost:11434/v1") is False

    def test_qwen_local_ollama_v1_no_tools(self):
        assert _compute_is_api_model("qwen2.5:14b", "http://localhost:11434/v1") is False

    def test_lan_ollama_v1_no_tools_by_port(self):
        assert _compute_is_api_model("gemma4:e2b", "http://192.168.1.50:11434/v1") is False

    def test_ollama_openai_compat_url_detector(self):
        assert _is_ollama_openai_compat_url("http://localhost:11434/v1") is True
        assert _is_ollama_openai_compat_url("http://localhost:11434/v1/chat/completions") is True
        assert _is_ollama_openai_compat_url("http://localhost:8000/v1") is False

    # --- cloud API cases (must still get tool schemas) ---

    def test_deepseek_cloud_api_gets_tools(self):
        result = _compute_is_api_model(
            "deepseek-chat", "https://api.deepseek.com/v1"
        )
        assert result is True, (
            "api.deepseek.com must be treated as tool-capable via _API_HOSTS"
        )

    def test_deepseek_v3_cloud_gets_tools(self):
        assert _compute_is_api_model("deepseek-v3", "https://api.deepseek.com/v1") is True

    def test_deepseek_v2_cloud_gets_tools(self):
        assert _compute_is_api_model("deepseek-v2.5", "https://api.deepseek.com/v1") is True

    # --- endpoint_supports override takes priority ---

    def test_endpoint_supports_true_overrides_blocklist(self):
        """A user who explicitly sets supports_tools=True on their endpoint
        can force tool schemas even for deepseek-r1 (e.g. custom server)."""
        result = _compute_is_api_model(
            "deepseek-r1:7b", "http://localhost:11434/v1", endpoint_supports=True
        )
        assert result is True

    def test_endpoint_supports_true_overrides_local_ollama_v1_default(self):
        result = _compute_is_api_model(
            "gemma4:e2b", "http://localhost:11434/v1", endpoint_supports=True
        )
        assert result is True

    def test_endpoint_supports_false_overrides_cloud(self):
        """supports_tools=False on an endpoint gates even cloud APIs."""
        result = _compute_is_api_model(
            "deepseek-chat", "https://api.deepseek.com/v1", endpoint_supports=False
        )
        assert result is False

    # --- other local OpenAI-compatible servers unaffected ---

    def test_qwen_local_non_ollama_still_gets_tools(self):
        assert _compute_is_api_model("qwen2.5:14b", "http://localhost:8000/v1") is True

    def test_llama_local_non_ollama_gets_tools_via_host(self):
        assert _compute_is_api_model("llama3.2:3b", "http://localhost:8000/v1") is True


class TestApiHostsContainsDeepSeek:
    def test_api_deepseek_com_in_api_hosts(self):
        assert "api.deepseek.com" in _API_HOSTS

    def test_deepseek_com_in_api_hosts(self):
        assert "deepseek.com" in _API_HOSTS

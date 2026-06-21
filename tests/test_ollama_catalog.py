"""Tests for services/hwfit/ollama_catalog.py"""

import json
from unittest.mock import MagicMock, patch

import pytest

from services.hwfit.ollama_catalog import (
    _infer_context,
    _parse_param_count,
    fetch_ollama_models,
    invalidate_cache,
)


class TestParseParamCount:
    def test_billions(self):
        assert _parse_param_count("7B") == "7B"

    def test_lowercase(self):
        assert _parse_param_count("3.2b") == "3.2B"

    def test_decimal(self):
        assert _parse_param_count("70.6B") == "70.6B"

    def test_empty(self):
        assert _parse_param_count("") == ""

    def test_no_suffix_defaults_b(self):
        assert _parse_param_count("8") == "8B"


class TestInferContext:
    def test_llama3_family(self):
        assert _infer_context({"family": "llama3", "families": ["llama3"]}) == 131072

    def test_gemma_family(self):
        assert _infer_context({"family": "gemma", "families": []}) == 8192

    def test_qwen2_family(self):
        assert _infer_context({"families": ["qwen2"]}) == 32768

    def test_unknown_family_defaults(self):
        assert _infer_context({"family": "unknown_future_model"}) == 4096

    def test_empty_details(self):
        assert _infer_context({}) == 4096


class TestFetchOllamaModels:
    def setup_method(self):
        invalidate_cache()

    def _mock_response(self, models_list):
        payload = json.dumps({"models": models_list}).encode()
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.read = MagicMock(return_value=payload)
        return mock

    def test_returns_empty_for_remote_host(self):
        result = fetch_ollama_models(host="user@remoteserver")
        assert result == []

    def test_returns_empty_when_ollama_not_running(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = fetch_ollama_models()
        assert result == []

    def test_parses_single_model(self):
        payload = [
            {
                "name": "llama3.2:latest",
                "size": 2019393189,
                "details": {
                    "format": "gguf",
                    "family": "llama3",
                    "parameter_size": "3.2B",
                    "quantization_level": "Q4_K_M",
                },
            }
        ]
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            result = fetch_ollama_models()
        assert len(result) == 1
        m = result[0]
        assert m["name"] == "llama3.2:latest"
        assert m["parameter_count"] == "3.2B"
        assert m["quant"] == "Q4_K_M"
        assert m["is_gguf"] is True
        assert m["is_ollama"] is True
        assert m["backend"] == "ollama"
        assert m["_source"] == "ollama"
        assert m["context_length"] == 131072

    def test_deduplicates_models(self):
        payload = [
            {"name": "phi3:mini", "size": 0, "details": {"parameter_size": "3.8B"}},
            {"name": "phi3:mini", "size": 0, "details": {"parameter_size": "3.8B"}},
        ]
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            result = fetch_ollama_models()
        assert len(result) == 1

    def test_returns_multiple_models(self):
        payload = [
            {"name": "llama3.1:8b", "size": 0, "details": {"parameter_size": "8B", "family": "llama3"}},
            {"name": "gemma2:9b", "size": 0, "details": {"parameter_size": "9B", "family": "gemma2"}},
        ]
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            result = fetch_ollama_models()
        names = {m["name"] for m in result}
        assert "llama3.1:8b" in names
        assert "gemma2:9b" in names

    def test_gguf_sources_set_for_ranker(self):
        """fit.py checks gguf_sources for GGUF detection — must be non-empty."""
        payload = [
            {"name": "mistral:7b", "size": 0, "details": {"parameter_size": "7B", "quantization_level": "Q4_0"}},
        ]
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            result = fetch_ollama_models()
        assert result[0]["gguf_sources"]  # truthy / non-empty

    def test_cache_is_used_on_second_call(self):
        payload = [{"name": "llama3.2:latest", "size": 0, "details": {"parameter_size": "3.2B"}}]
        call_count = {"n": 0}

        def counting_open(url, timeout):
            call_count["n"] += 1
            return self._mock_response(payload)

        with patch("urllib.request.urlopen", side_effect=counting_open):
            fetch_ollama_models()
            fetch_ollama_models()

        assert call_count["n"] == 1, "expected cache hit on second call"

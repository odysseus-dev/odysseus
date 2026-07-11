"""Tests for endpoint_resolver — request header construction."""
from src.endpoint_resolver import build_headers


class TestBuildHeaders:
    def test_no_key(self):
        assert build_headers(None, "https://api.openai.com/v1") == {}

    def test_openai_bearer(self):
        assert build_headers("sk-abc", "https://api.openai.com/v1") == {"Authorization": "Bearer sk-abc"}

    def test_anthropic_headers(self):
        assert build_headers("sk-ant-abc", "https://api.anthropic.com") == {"x-api-key": "sk-ant-abc", "anthropic-version": "2023-06-01"}

    def test_empty_key(self):
        assert build_headers("", "https://api.openai.com/v1") == {}

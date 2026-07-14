"""Regression: post-image-gen LLM wait must not probe /v1/v1/models."""

from src.agent_loop import _models_url_from_endpoint


def test_models_url_from_chat_completions_endpoint():
    url = _models_url_from_endpoint("http://host.docker.internal:8000/v1/chat/completions")
    assert url == "http://host.docker.internal:8000/v1/models"
    assert "/v1/v1/" not in url


def test_models_url_from_v1_base():
    url = _models_url_from_endpoint("http://127.0.0.1:8000/v1")
    assert url == "http://127.0.0.1:8000/v1/models"

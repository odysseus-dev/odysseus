"""Regression: research endpoint model selection must honor pinned model IDs.

When research is started against an explicitly chosen endpoint with no model
specified, the handler picked the endpoint's default model from cached_models
only. Admin-pinned model IDs (cloud deployment IDs) live in pinned_models and
never appear in cached_models, so a pinned-only endpoint yielded an empty model
and the research run failed with model="". `_endpoint_default_model` merges
cached + pinned before picking the first chat model.
"""
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

# research_routes pulls a few app modules at import; stub the heavy/optional
# ones so the pure helper is importable in the test venv.
for _name in ["pyotp"]:
    if _name not in sys.modules:
        try:
            __import__(_name)
        except Exception:
            sys.modules[_name] = MagicMock()

from routes.research_routes import _endpoint_default_model


def test_pinned_only_endpoint_yields_pinned_model():
    ep = SimpleNamespace(cached_models=None, pinned_models='["my-cloud-deploy-id"]')
    assert _endpoint_default_model(ep) == "my-cloud-deploy-id"


def test_cached_first_chat_model_still_used():
    ep = SimpleNamespace(
        cached_models='["text-embedding-3-large", "gpt-4o"]',
        pinned_models=None,
    )
    # skips the embedding model, picks the chat model
    assert _endpoint_default_model(ep) == "gpt-4o"


def test_cached_and_pinned_merge():
    ep = SimpleNamespace(
        cached_models='["text-embedding-3-large"]',
        pinned_models='["my-cloud-deploy-id"]',
    )
    # cached has only a non-chat model; pinned chat id wins
    assert _endpoint_default_model(ep) == "my-cloud-deploy-id"


def test_no_models_returns_empty():
    ep = SimpleNamespace(cached_models=None, pinned_models=None)
    assert _endpoint_default_model(ep) == ""

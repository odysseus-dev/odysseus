import pytest

from src import ai_interaction
from routes.chat_routes import (
    _requested_media_generation_from_context,
    _requested_media_generation_kind,
    _requested_media_generation_kind_from_context,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("make me a dog image", "image"),
        ("produce an image of a dog", "image"),
        ("draw me a cartoon dog", "image"),
        ("create a 10 second product video", "video"),
        ("compose a cinematic soundtrack", "music"),
        ("write a 30 second jingle", "music"),
        ("it did this instead of producing an image", None),
        ("write an SVG of a dog", None),
        ("give me a prompt for image generation", None),
        ("why did it generate a prompt instead of a photo", None),
    ],
)
def test_requested_media_generation_kind(message, expected):
    assert _requested_media_generation_kind(message) == expected


def test_image_model_hint_prefers_image_capable_provider_models(monkeypatch):
    class Endpoint:
        def __init__(self, name, base_url, pinned_models=None):
            self.name = name
            self.base_url = base_url
            self.pinned_models = pinned_models

    rows = [
        (Endpoint("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"), [
            "models/gemini-2.5-flash",
            "models/gemini-3.1-flash-image",
            "models/imagen-4.0-generate-001",
        ]),
        (Endpoint("OpenAI", "https://api.openai.com/v1"), [
            "gpt-5.5",
            "gpt-image-1",
        ]),
        (Endpoint("localhost:1234", "http://localhost:1234/v1"), [
            "flux.1-dev",
            "qwen-image-edit-2511",
        ]),
    ]
    monkeypatch.setattr(ai_interaction, "_cached_models_for_hint", lambda owner=None: rows)

    assert ai_interaction._image_model_hint_from_prompt("use Gemini to create an image") == "models/gemini-3.1-flash-image"
    assert ai_interaction._image_model_hint_from_prompt("use ChatGPT/OpenAI to create an image") == "gpt-image-1"
    assert ai_interaction._image_model_hint_from_prompt("use flux for this image") == "flux.1-dev"


def test_image_model_hint_uses_pinned_gemini_image_model(monkeypatch):
    class Endpoint:
        def __init__(self, name, base_url, cached_models=None, pinned_models=None):
            self.name = name
            self.base_url = base_url
            self.cached_models = cached_models
            self.pinned_models = pinned_models

    rows = [
        (Endpoint(
            "Gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            cached_models='["models/gemini-2.5-flash"]',
            pinned_models='["gemini-image-pro"]',
        ), ai_interaction._model_ids_from_endpoint_fields(
            Endpoint(
                "Gemini",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                cached_models='["models/gemini-2.5-flash"]',
                pinned_models='["gemini-image-pro"]',
            ),
            "cached_models",
            "pinned_models",
        )),
    ]
    monkeypatch.setattr(ai_interaction, "_cached_models_for_hint", lambda owner=None: rows)

    assert ai_interaction._image_model_hint_from_prompt("use gemini to create an image") == "gemini-image-pro"


def test_image_model_hint_falls_back_to_native_gemini_image_api(monkeypatch):
    class Endpoint:
        def __init__(self, name, base_url):
            self.name = name
            self.base_url = base_url

    rows = [
        (Endpoint("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"), [
            "models/gemini-2.5-flash",
            "models/gemini-2.5-pro",
        ]),
    ]
    monkeypatch.setattr(ai_interaction, "_cached_models_for_hint", lambda owner=None: rows)

    assert ai_interaction._image_model_hint_from_prompt("use gemini to create an image") == "gemini-3-pro-image"


def test_gemini_image_api_helpers_handle_openai_compatible_base():
    url = ai_interaction._gemini_generate_content_url(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "gemini-image-pro",
    )

    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image:generateContent"
    assert ai_interaction._extract_gemini_image_b64({
        "candidates": [{
            "content": {
                "parts": [{
                    "inlineData": {"mimeType": "image/png", "data": "abc123"}
                }]
            }
        }]
    }) == "abc123"


def test_media_generation_continuation_reuses_last_explicit_request():
    messages = [
        {"role": "user", "content": "produce an image of a friendly dog"},
        {"role": "assistant", "content": "Here is SVG code instead."},
        {"role": "user", "content": "use the skill and try again"},
    ]

    assert _requested_media_generation_kind_from_context("use the skill and try again", messages) == "image"
    assert _requested_media_generation_from_context("use the skill and try again", messages) == (
        "image",
        "produce an image of a friendly dog",
    )


def test_media_generation_continuation_requires_missed_assistant_response():
    messages = [
        {"role": "user", "content": "produce an image of a friendly dog"},
        {"role": "assistant", "content": "Image generation complete."},
        {"role": "user", "content": "try again"},
    ]

    assert _requested_media_generation_kind_from_context("try again", messages) is None


def test_media_generation_continuation_uses_immediate_prior_turn_only():
    messages = [
        {"role": "user", "content": "produce an image of a friendly dog"},
        {"role": "assistant", "content": "Here is SVG code instead."},
        {"role": "user", "content": "why did that happen?"},
        {"role": "assistant", "content": "The model ignored the media tool."},
        {"role": "user", "content": "try again"},
    ]

    assert _requested_media_generation_kind_from_context("try again", messages) is None


def test_media_generation_continuation_without_prior_request_stays_chat():
    messages = [
        {"role": "user", "content": "why did the model answer with SVG?"},
        {"role": "assistant", "content": "It missed the tool."},
        {"role": "user", "content": "try again"},
    ]

    assert _requested_media_generation_kind_from_context("try again", messages) is None

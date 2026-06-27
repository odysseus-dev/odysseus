import asyncio
import base64
from types import SimpleNamespace

import pytest

from src import ai_interaction
from routes.chat_routes import (
    _direct_media_request_for_session,
    _generate_direct_media,
)


@pytest.mark.parametrize(
    "message",
    [
        "make me a dog image",
        "produce an image of a dog",
        "create a 10 second product video",
        "compose a cinematic soundtrack",
    ],
)
def test_media_words_do_not_route_text_model_to_generation(message):
    sess = SimpleNamespace(model="qwen3.5:latest", endpoint_url="http://localhost:11434/v1/chat/completions")

    assert _direct_media_request_for_session(sess, message, [{"role": "user", "content": message}]) is None


def test_selected_video_model_routes_to_video_generation():
    sess = SimpleNamespace(model="veo-3.1-generate-preview", endpoint_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")

    assert _direct_media_request_for_session(sess, "A slow dolly shot of a glass greenhouse", []) == (
        "video",
        "A slow dolly shot of a glass greenhouse",
    )


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


def test_generate_image_prefers_current_session_image_model_over_default(monkeypatch, tmp_path):
    from src import database, settings
    import httpx

    captured = {}
    manager = SimpleNamespace(
        sessions={
            "s1": SimpleNamespace(
                model="flux2-klein:4b",
                endpoint_url="http://127.0.0.1:11434/v1/chat/completions",
                owner="alice",
            )
        }
    )

    monkeypatch.setattr(ai_interaction, "get_session_manager", lambda: manager)
    monkeypatch.setattr(ai_interaction, "_image_model_hint_from_prompt", lambda prompt, owner=None: "gemini-3-pro-image-preview")
    monkeypatch.setattr(ai_interaction, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(
        settings,
        "load_settings",
        lambda: {"image_model": "gemini-3-pro-image-preview", "image_quality": "high"},
    )

    def fake_resolve_model(spec, owner=None):
        captured["resolved_spec"] = spec
        captured["owner"] = owner
        return "http://127.0.0.1:11434/v1/chat/completions", "x/flux2-klein:4b", {}

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve_model)

    class FakeGalleryDb:
        def add(self, item):
            captured["gallery_model"] = getattr(item, "model", "")

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeGalleryDb())

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "data": [{
                    "b64_json": base64.b64encode(b"fake-png-bytes").decode("ascii")
                }]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["payload"] = dict(json or {})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        ai_interaction.do_generate_image(
            "create an image of a micro bulldog all grey, very cute",
            session_id="s1",
            owner="alice",
        )
    )

    assert result["image_model"] == "x/flux2-klein:4b"
    assert captured["resolved_spec"] == "flux2-klein:4b"
    assert captured["owner"] == "alice"
    assert captured["url"] == "http://127.0.0.1:11434/v1/images/generations"
    assert captured["payload"]["model"] == "x/flux2-klein:4b"
    assert captured["payload"]["response_format"] == "b64_json"
    assert "quality" not in captured["payload"]
    assert captured["gallery_model"] == "x/flux2-klein:4b"


def test_direct_image_generation_does_not_fallback_when_session_model_selected(monkeypatch):
    from src import runcomfy_media

    async def fake_generate_image(content, session_id=None, owner=None):
        return {"error": "Image generation failed (400): endpoint rejected request"}

    async def fail_runcomfy(*args, **kwargs):
        raise AssertionError("selected image models should not fall back to media fallback")

    monkeypatch.setattr(ai_interaction, "_session_selected_image_model", lambda session_id, owner=None: "flux2-klein:4b")
    monkeypatch.setattr(ai_interaction, "do_generate_image", fake_generate_image)
    monkeypatch.setattr(runcomfy_media, "generate_runcomfy_media", fail_runcomfy)

    result = asyncio.run(
        _generate_direct_media(
            "image",
            "create an image of a micro bulldog",
            "s1",
            "alice",
        )
    )

    assert result == {"error": "Image generation failed (400): endpoint rejected request"}


def test_agent_generate_image_tool_does_not_fallback_when_session_model_selected(monkeypatch):
    from src import runcomfy_media
    from src.tool_execution import execute_tool_block
    from src.agent_tools import ToolBlock

    async def fake_generate_image(content, session_id=None, owner=None):
        return {"error": "Image generation failed (400): endpoint rejected request"}

    async def fail_runcomfy(*args, **kwargs):
        raise AssertionError("selected image models should not fall back to media fallback")

    monkeypatch.setattr(ai_interaction, "_session_selected_image_model", lambda session_id, owner=None: "flux2-klein:4b")
    monkeypatch.setattr(ai_interaction, "do_generate_image", fake_generate_image)
    monkeypatch.setattr(runcomfy_media, "generate_runcomfy_media", fail_runcomfy)

    _desc, result = asyncio.run(
        execute_tool_block(
            ToolBlock("generate_image", "create an image of a micro bulldog"),
            session_id="s1",
            owner="alice",
        )
    )

    assert result["error"] == "Image generation failed (400): endpoint rejected request"
    assert result["exit_code"] == 1

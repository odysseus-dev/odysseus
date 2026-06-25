import sys
import asyncio
for mod_name in ["src.endpoint_resolver", "src.database", "core.database"]:
    _mod = sys.modules.get(mod_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(mod_name, None)

import json
from types import SimpleNamespace

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules

clear_fake_endpoint_resolver_modules("routes.chat_routes")

from routes import chat_routes


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        return self

    def all(self):
        return list(self.rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def query(self, model):
        return _FakeQuery(self.rows)

    def close(self):
        self.closed = True


def _session(model="qwen3.5:latest", endpoint_url="http://localhost:11434/v1/chat/completions"):
    return SimpleNamespace(model=model, endpoint_url=endpoint_url)


def _endpoint(base_url, model_type="image", models=None):
    cached_models = None if models is None else json.dumps(models)
    return SimpleNamespace(
        id="endpoint-id",
        name=base_url,
        base_url=base_url,
        model_type=model_type,
        is_enabled=True,
        cached_models=cached_models,
        hidden_models=None,
        pinned_models=None,
        provider_auth_id=None,
        api_key=None,
    )


def test_image_model_prefix_routes_to_image_generation_without_endpoint_lookup(monkeypatch):
    def fail_if_called():
        raise AssertionError("prefixed image models should not need a DB lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)

    assert chat_routes._is_image_generation_session(_session(model="dall-e-3"))


def test_image_endpoint_does_not_catch_text_model_on_different_path(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1/images", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert not chat_routes._is_image_generation_session(_session())
    assert db.closed


def test_image_endpoint_cache_must_contain_selected_model(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert not chat_routes._is_image_generation_session(_session(model="qwen3.5:latest"))


def test_matching_image_endpoint_routes_selected_image_model(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert chat_routes._is_image_generation_session(_session(model="sdxl-local"))


def test_image_generation_session_respects_explicit_video_request(monkeypatch):
    def fail_if_called():
        raise AssertionError("prefixed image models should not need a DB lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)
    sess = _session(model="gemini-image-pro")

    request = chat_routes._direct_media_request_for_session(
        sess,
        "Create a video of a micro bulldog",
        [{"role": "user", "content": "Create a video of a micro bulldog"}],
    )

    assert request == ("video", "Create a video of a micro bulldog")


def test_image_generation_session_defaults_to_image_without_explicit_media_kind(monkeypatch):
    def fail_if_called():
        raise AssertionError("prefixed image models should not need a DB lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)
    sess = _session(model="gemini-image-pro")

    request = chat_routes._direct_media_request_for_session(
        sess,
        "A clean product shot of a stainless watch",
        [{"role": "user", "content": "A clean product shot of a stainless watch"}],
    )

    assert request == ("image", "A clean product shot of a stainless watch")


def test_image_generation_session_does_not_generate_for_image_visibility_question(monkeypatch):
    def fail_if_called():
        raise AssertionError("visibility questions should not need image endpoint lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)
    sess = _session(model="gemini-image-pro")

    request = chat_routes._direct_media_request_for_session(
        sess,
        "can you see the image above",
        [{"role": "user", "content": "can you see the image above"}],
    )

    assert request is None


def test_image_messages_use_vision_fallbacks_before_chat_fallbacks(monkeypatch):
    from src import endpoint_resolver

    seen = []

    def fake_vision_fallbacks(owner=None):
        seen.append(("vision", owner))
        return [("http://vision.test/chat/completions", "vision-fallback", {"V": "1"})]

    def fake_chat_fallbacks(owner=None):
        seen.append(("chat", owner))
        return [("http://chat.test/chat/completions", "chat-fallback", {"C": "1"})]

    monkeypatch.setattr(endpoint_resolver, "resolve_vision_fallback_candidates", fake_vision_fallbacks)
    monkeypatch.setattr(endpoint_resolver, "resolve_chat_fallback_candidates", fake_chat_fallbacks)

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "can you view this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }]

    assert chat_routes._resolve_chat_stream_fallback_candidates(messages, owner="alice") == [
        ("http://vision.test/chat/completions", "vision-fallback", {"V": "1"}),
        ("http://chat.test/chat/completions", "chat-fallback", {"C": "1"}),
    ]
    assert seen == [("vision", "alice"), ("chat", "alice")]


def test_text_messages_only_use_chat_fallbacks(monkeypatch):
    from src import endpoint_resolver

    seen = []

    def fake_vision_fallbacks(owner=None):
        seen.append(("vision", owner))
        return [("http://vision.test/chat/completions", "vision-fallback", {})]

    def fake_chat_fallbacks(owner=None):
        seen.append(("chat", owner))
        return [("http://chat.test/chat/completions", "chat-fallback", {})]

    monkeypatch.setattr(endpoint_resolver, "resolve_vision_fallback_candidates", fake_vision_fallbacks)
    monkeypatch.setattr(endpoint_resolver, "resolve_chat_fallback_candidates", fake_chat_fallbacks)

    messages = [{"role": "user", "content": "hello"}]

    assert chat_routes._resolve_chat_stream_fallback_candidates(messages, owner="alice") == [
        ("http://chat.test/chat/completions", "chat-fallback", {}),
    ]
    assert seen == [("chat", "alice")]


def test_image_messages_auto_detect_enabled_vision_model_when_no_chain(monkeypatch):
    from src import chat_helpers, endpoint_resolver

    db = _FakeDb([
        _endpoint(
            "http://127.0.0.1:11434/v1",
            model_type="llm",
            models=["deepseek-r1:8b", "minicpm-v4.6:1b", "qwen3.6:latest"],
        ),
        _endpoint(
            "http://127.0.0.1:8102/v1",
            model_type="image",
            models=["stable-diffusion-v1-5-inpainting-onnx-fp16"],
        ),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat_routes, "_resolve_configured_vision_primary_candidate", lambda owner=None: [])
    monkeypatch.setattr(endpoint_resolver, "resolve_vision_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(endpoint_resolver, "resolve_chat_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(
        chat_helpers,
        "model_supports_vision",
        lambda model, endpoint_url="": model == "minicpm-v4.6:1b",
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "can you view this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }]

    assert chat_routes._resolve_chat_stream_fallback_candidates(messages, owner=None) == [
        ("http://127.0.0.1:11434/v1/chat/completions", "minicpm-v4.6:1b", {}),
    ]


def test_selected_vision_model_stays_primary_for_image_turn():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "can you see this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }]

    assert chat_routes._should_prefer_vision_fallback(
        messages,
        _session(model="gpt-5.4-mini"),
        [("http://vision.test/chat/completions", "gpt-4o", {})],
    ) is False


def test_text_selected_model_prefers_vision_fallback_for_image_turn():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "can you see this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }]

    assert chat_routes._should_prefer_vision_fallback(
        messages,
        _session(model="plain-text-model"),
        [("http://vision.test/chat/completions", "gpt-4o", {})],
    ) is True


def test_preprocess_keeps_raw_image_when_vision_fallback_exists(tmp_path, monkeypatch):
    from src.chat_handler import ChatHandler

    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    class FakeUploadHandler:
        def resolve_upload(self, att_id, owner=None):
            assert owner == "alice"
            return {
                "id": att_id,
                "name": "photo.png",
                "mime": "image/png",
                "size": image_path.stat().st_size,
                "path": str(image_path),
            }

        def is_image_file(self, name, mime=""):
            return True

        def _inside_upload_dir(self, path):
            return True

    monkeypatch.setattr("src.chat_handler.model_supports_vision", lambda model, endpoint_url="": False)
    monkeypatch.setattr(
        "src.chat_handler._resolve_available_vision_route",
        lambda owner=None: ("http://vision.test/chat/completions", "gpt-4o", {}),
    )

    handler = ChatHandler(None, None, None, None, None, FakeUploadHandler())
    sess = _session(model="plain-text-model", endpoint_url="http://text.test/v1/chat/completions")
    sess.id = "s1"
    sess.owner = "alice"

    enhanced, user_content, _text_ctx, _yt, attachment_meta = asyncio.run(
        handler.preprocess_message("can you see this image?", ["img1"], sess)
    )

    assert "[Image attached: photo.png]" in enhanced
    assert isinstance(user_content, list)
    assert any(item.get("type") == "image_url" for item in user_content)
    assert attachment_meta[0]["vision_model"] == "gpt-4o"

from pathlib import Path

import pytest

import routes.chat_helpers as chat_helpers
import services.search.images as images_module
import src.settings as settings_module

ROOT = Path(__file__).resolve().parents[1]


def _settings(monkeypatch, *, images=True, provider="searxng"):
    values = {"search_images": images, "search_provider": provider}
    monkeypatch.setattr(
        settings_module,
        "get_setting",
        lambda key, default=None: values.get(key, default),
    )


@pytest.mark.asyncio
async def test_generate_image_results_requires_enabled_setting(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_SPEC_IMAGES", raising=False)
    _settings(monkeypatch, images=False)

    def fail(*a, **k):
        raise AssertionError("image search should not run when the setting is off")

    monkeypatch.setattr(images_module, "image_search", fail)

    assert await chat_helpers.generate_image_results("show me a red panda") == []


@pytest.mark.asyncio
async def test_generate_image_results_requires_searxng(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_SPEC_IMAGES", raising=False)
    _settings(monkeypatch, provider="duckduckgo")

    def fail(*a, **k):
        raise AssertionError("image search should only run for SearXNG")

    monkeypatch.setattr(images_module, "image_search", fail)

    assert await chat_helpers.generate_image_results("show me a red panda") == []


@pytest.mark.asyncio
async def test_generate_image_results_searches_for_web_turn(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_SPEC_IMAGES", raising=False)
    _settings(monkeypatch)
    calls = []

    def fake_image_search(query, count=None, time_filter=None, timeout=15):
        calls.append((query, count, time_filter, timeout))
        return [{"title": "Red panda", "thumbnail": "https://example.com/p.jpg"}]

    monkeypatch.setattr(images_module, "image_search", fake_image_search)

    out = await chat_helpers.generate_image_results("show me a red panda", count=99)

    assert out == [{"title": "Red panda", "thumbnail": "https://example.com/p.jpg"}]
    assert calls == [("show me a red panda", 24, None, 8)]


@pytest.mark.asyncio
async def test_generate_image_results_defaults_to_eleven(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_SPEC_IMAGES", raising=False)
    monkeypatch.delenv("ODYSSEUS_SPEC_IMAGE_COUNT", raising=False)
    _settings(monkeypatch)
    calls = []

    def fake_image_search(query, count=None, time_filter=None, timeout=15):
        calls.append((query, count, time_filter, timeout))
        return []

    monkeypatch.setattr(images_module, "image_search", fake_image_search)

    await chat_helpers.generate_image_results("show me a red panda")

    assert calls == [("show me a red panda", 11, None, 8)]


@pytest.mark.asyncio
async def test_generate_image_results_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_SPEC_IMAGES", "0")
    _settings(monkeypatch)

    def fail(*a, **k):
        raise AssertionError("image search should not run when disabled")

    monkeypatch.setattr(images_module, "image_search", fail)

    assert await chat_helpers.generate_image_results("show me a cat") == []


@pytest.mark.asyncio
async def test_generate_image_results_fails_open(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_SPEC_IMAGES", raising=False)
    _settings(monkeypatch)

    def fail(*a, **k):
        raise OSError("searxng down")

    monkeypatch.setattr(images_module, "image_search", fail)

    assert await chat_helpers.generate_image_results("show me a cat") == []


def test_save_assistant_response_persists_images(monkeypatch):
    import core.database as database

    picked = [{"title": "Red panda", "thumbnail": "https://example.com/p.jpg"}]
    monkeypatch.setattr(database, "update_session_last_accessed", lambda *a, **k: None)
    monkeypatch.setattr(chat_helpers, "_append_sft_trace_record", lambda **k: None)

    class Message:
        def __init__(self, role, content, metadata=None):
            self.role = role
            self.content = content
            self.metadata = metadata or {}

    class Session:
        model = "test-model"

        def __init__(self):
            self.history = [Message("user", "show me a red panda")]

        def add_message(self, message):
            self.history.append(message)

    class Manager:
        def save_sessions(self):
            pass

    sess = Session()
    chat_helpers.save_assistant_response(
        sess,
        Manager(),
        "session-1",
        "Here is the answer.",
        {"model": "test-model"},
        images=picked,
    )

    assert sess.history[-1].metadata["images"] == picked


def test_image_search_settings_and_results_wiring():
    renderer = (ROOT / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    routes = (ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")
    settings = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "03-agent-chat.css").read_text(encoding="utf-8")
    defaults = (ROOT / "src" / "settings.py").read_text(encoding="utf-8")
    helpers = (ROOT / "routes" / "chat_helpers.py").read_text(encoding="utf-8")

    assert "export function buildImageResults" in renderer
    assert "chat-image-card" in renderer
    assert "chat-image-skeleton" in renderer
    assert "chat-image-more-label" in renderer
    assert "chat-images-more" in renderer
    assert "chat-image-skeleton" in css
    assert "chat-image-open" in renderer
    assert 'class="chat-image-source" href=' in renderer
    assert ".chat-image-real:nth-child(n + 3)" in css
    assert ".chat-image-source:hover" in css
    assert '"search_images": True' in defaults
    assert "set-searchImages" in index
    assert "search_images" in settings
    assert "overflow-image-btn" not in index
    assert "image-toggle" not in index
    assert "_syncImageToggle" not in app
    assert "fd.append('image_search'" not in chat
    assert "json.type === 'images'" in chat
    assert "closest('.chat-images-more')" in chat
    assert "chatRenderer.buildImageResults(_imagesData) : '')" in chat
    assert "saved.metadata?.images?.length && !_imagesData" in chat
    assert "_syncImageGallery" in chat
    assert "_collapsedImageVisibleCount" in chat
    assert "gridTemplateColumns" in chat
    assert "_initImageGallerySync" in chat
    assert "if not _search_enabled or not web_sources:" in routes
    assert "generate_image_results(message)" in routes
    assert "generate_image_results(message, force=True)" not in routes
    assert 'get_setting("search_images"' in helpers
    assert 'get_setting("search_provider"' in helpers

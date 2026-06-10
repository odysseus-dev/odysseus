"""Regression: the default chat model must be the first CHAT model, not the
raw first visible id.

GET /api/default-chat's last-resort branch (new user, no saved default_model)
picked `visible[0]` from the endpoint's models. `_visible_models` returns ids
in provider/probe order with no chat-capability filter, so for OpenAI-style
endpoints that list `text-embedding-3-large` (or tts/whisper/dall-e) ahead of
`gpt-4o`, the default chat model became a non-chat model and the first send
400'd. Every other default-selection site uses `_first_chat_model`; this one
must too.
"""
import sys
import types
from unittest.mock import MagicMock

# Match tests/test_model_routes.py: stub core.database, keep real endpoint_resolver.
if "core.database" not in sys.modules:
    _core_db = types.ModuleType("core.database")
    for _name in [
        "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
        "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
        "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
    ]:
        setattr(_core_db, _name, MagicMock())
    sys.modules["core.database"] = _core_db

_er = sys.modules.get("src.endpoint_resolver")
if _er is not None and not getattr(_er, "__file__", None):
    sys.modules.pop("src.endpoint_resolver", None)
    sys.modules.pop("routes.model_routes", None)

from routes.model_routes import _default_chat_model


def test_default_chat_model_skips_embedding_first():
    # Provider lists an embedding model first, then a chat model.
    visible = ["text-embedding-3-large", "gpt-4o"]
    assert _default_chat_model(visible) == "gpt-4o"


def test_default_chat_model_skips_tts_and_image():
    visible = ["tts-1", "dall-e-3", "whisper-1", "gpt-4o-mini"]
    assert _default_chat_model(visible) == "gpt-4o-mini"


def test_default_chat_model_falls_back_to_first_when_all_non_chat():
    visible = ["text-embedding-3-large", "text-embedding-3-small"]
    assert _default_chat_model(visible) == "text-embedding-3-large"


def test_default_chat_model_empty():
    assert _default_chat_model([]) == ""

import pytest
from types import SimpleNamespace

from src.chat_handler import ChatHandler


class _UploadHandler:
    def resolve_upload(self, *_args, **_kwargs):
        raise AssertionError("attachments must not be resolved when tool preprocessing is disabled")

    def is_image_file(self, *_args, **_kwargs):
        raise AssertionError("images must not be inspected when tool preprocessing is disabled")


@pytest.mark.asyncio
async def test_preprocess_can_skip_external_context_and_attachment_work(monkeypatch):
    async def _fail_transcript(*_args, **_kwargs):
        raise AssertionError("YouTube transcripts must not be fetched")

    async def _fail_comments(*_args, **_kwargs):
        raise AssertionError("YouTube comments must not be fetched")

    monkeypatch.setattr("src.chat_handler.extract_transcript_async", _fail_transcript)
    monkeypatch.setattr("src.chat_handler.fetch_youtube_comments", _fail_comments)
    monkeypatch.setattr(
        "src.chat_handler.model_supports_vision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision support must not be probed")
        ),
    )

    handler = ChatHandler(
        session_manager=None,
        memory_manager=None,
        chat_processor=None,
        research_handler=None,
        preset_manager=None,
        upload_handler=_UploadHandler(),
    )
    sess = SimpleNamespace(model="text-only", endpoint_url="", owner="user", id="session")

    enhanced, user_content, text_ctx, youtube, attachment_meta = await handler.preprocess_message(
        "Do not use tools. https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ["image-id"],
        sess,
        auto_opened_docs=[],
        allow_tool_preprocessing=False,
    )

    assert enhanced.startswith("Do not use tools.")
    assert user_content == enhanced
    assert text_ctx == enhanced
    assert youtube == []
    assert attachment_meta == []

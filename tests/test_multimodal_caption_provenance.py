"""Regression coverage for PR #6148 review findings:

1. Background captioning must not fire without a confirmed answering
   route (explicit-fallback routing can switch which candidate actually
   answered — sending images to an unconfirmed route is a privacy/routing
   risk).
2. It must not overwrite an existing caption/user correction.
3. Generated captions must be distinguishable from genuine user
   corrections when read back into a later prompt (the .autogen marker),
   so unreviewed model output never gets the "treat as authoritative"
   label a real user correction gets.
4. An extensionless image must not get mislabeled as JPEG in the caption
   request when the resolved upload MIME says otherwise.
"""
import os

import pytest

from routes.chat_helpers import _caption_multimodal_image_attachments, run_post_response_tasks
from src.document_processor import describe_image_for_caption


class _FakeUploadHandler:
    def __init__(self, path: str, mime: str = "image/png"):
        self._path = path
        self._mime = mime

    def resolve_upload(self, att_id, owner=None):
        return {"id": att_id, "path": self._path, "mime": self._mime}


@pytest.mark.asyncio
async def test_caption_job_noop_without_endpoint_or_model(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"not-a-real-png-but-bytes-are-enough")
    handler = _FakeUploadHandler(str(img))
    calls = []

    async def _fake_describe(*args, **kwargs):
        calls.append((args, kwargs))
        return "should not be called"

    import src.document_processor as dp
    orig = dp.describe_image_for_caption
    dp.describe_image_for_caption = _fake_describe
    try:
        # No endpoint_url — must no-op.
        await _caption_multimodal_image_attachments(
            [{"id": "att1", "mime": "image/png"}], None, "some-model", {}, "alice", handler,
        )
        # No model — must no-op.
        await _caption_multimodal_image_attachments(
            [{"id": "att1", "mime": "image/png"}], "http://x", None, {}, "alice", handler,
        )
    finally:
        dp.describe_image_for_caption = orig
    assert calls == []


def test_run_post_response_tasks_skips_captioning_without_confirmed_route(monkeypatch):
    """caption_endpoint_url/caption_model default to None — the scheduling
    block must not queue an image-caption job when the caller can't confirm
    a single answering route (mirrors the agent-mode call site, which
    deliberately never passes these)."""
    from types import SimpleNamespace
    sess = SimpleNamespace(
        endpoint_url="http://x", model="m", headers={}, history=[], name="Test Session",
    )
    queued = []
    monkeypatch.setattr(
        "routes.chat_helpers._caption_multimodal_image_attachments",
        lambda *a, **k: queued.append((a, k)),
    )
    monkeypatch.setattr(
        "routes.chat_helpers._run_extraction_jobs_sequentially",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("routes.chat_helpers._spawn_bg", lambda coro: None)
    run_post_response_tasks(
        sess, session_manager=SimpleNamespace(), session_id="s1", message="hi", full_response="ok",
        last_metrics=None, uprefs={}, memory_manager=None, memory_vector=None, webhook_manager=None,
        allow_background_extraction=True,
        attachment_meta=[{"id": "att1", "mime": "image/png"}],
        caption_endpoint_url=None,  # not confirmed
        caption_model="m",
        upload_handler=object(),
    )
    assert queued == []


def test_run_post_response_tasks_skips_already_captioned_image(monkeypatch):
    """An attachment that already has a 'vision' entry (existing caption or
    user correction) must never be re-queued for captioning."""
    from types import SimpleNamespace
    sess = SimpleNamespace(endpoint_url="http://x", model="m", headers={}, history=[], name="Test Session")
    queued = []
    monkeypatch.setattr(
        "routes.chat_helpers._caption_multimodal_image_attachments",
        lambda atts, *a, **k: queued.append(atts),
    )
    monkeypatch.setattr("routes.chat_helpers._spawn_bg", lambda coro: None)
    run_post_response_tasks(
        sess, session_manager=SimpleNamespace(), session_id="s1", message="hi", full_response="ok",
        last_metrics=None, uprefs={}, memory_manager=None, memory_vector=None, webhook_manager=None,
        allow_background_extraction=True,
        attachment_meta=[{"id": "att1", "mime": "image/png", "vision": "already captioned"}],
        caption_endpoint_url="http://x",
        caption_model="m",
        upload_handler=object(),
    )
    # No job queued at all — the (mocked) list of image attachments should
    # never even reach _caption_multimodal_image_attachments.
    assert queued == []


@pytest.mark.asyncio
async def test_generated_caption_writes_autogen_marker(tmp_path, monkeypatch):
    img = tmp_path / "photo.png"
    img.write_bytes(b"bytes")
    handler = _FakeUploadHandler(str(img))

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    import src.constants as constants
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(tmp_path))

    async def _fake_describe(*args, **kwargs):
        return "a description"

    import src.document_processor as dp
    monkeypatch.setattr(dp, "describe_image_for_caption", _fake_describe)
    monkeypatch.setattr(
        "src.chat_handler._sync_upload_vision_to_gallery", lambda *a, **k: None,
    )

    await _caption_multimodal_image_attachments(
        [{"id": "att1", "mime": "image/png", "checksum_sha256": "abc"}],
        "http://x", "m", {}, "alice", handler,
    )

    cache_path = os.path.join(str(tmp_path), ".vision", "att1.txt")
    marker_path = cache_path + ".autogen"
    assert os.path.exists(cache_path), "caption should be cached"
    assert os.path.exists(marker_path), "generated caption must carry the .autogen provenance marker"


def test_describe_image_for_caption_extensionless_uses_resolved_mime(tmp_path, monkeypatch):
    """An extensionless upload recorded as image/png must not be sent to the
    vision model labeled jpeg."""
    img = tmp_path / "pasted_screenshot"  # no extension
    img.write_bytes(b"bytes")

    captured = {}

    async def _fake_llm_call_async(url, model, messages, headers=None, timeout=None):
        captured["messages"] = messages
        return "ok"

    import src.llm_core as llm_core
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm_call_async)

    import asyncio
    asyncio.run(describe_image_for_caption(str(img), "http://x", "m", {}, mime="image/png"))

    image_block = next(
        b for b in captured["messages"][0]["content"] if b.get("type") == "image_url"
    )
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,"), (
        "extensionless path with mime=image/png must not default to jpeg"
    )

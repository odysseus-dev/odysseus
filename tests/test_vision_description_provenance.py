"""Issue #5573 — the vision model's description must not read as the user's own words.

For a text-only chat model the description comes from a separate vision call and
is appended to the user's message. Unlabelled, the chat model answers the prose
instead of the image — reviewing the user's "writing" back to them.
"""

import importlib
from types import SimpleNamespace

import pytest

DESCRIPTION = "A chocolate cake, plated with a mirror glaze and gold leaf."
QUESTION = "What am I looking at?"


class _UploadHandler:
    def __init__(self, file_info):
        self._file_info = file_info

    def resolve_upload(self, att_id, owner=None):
        return self._file_info if att_id == self._file_info["id"] else None

    def is_image_file(self, *_args, **_kwargs):
        return True


def _no_vision_call(*_args, **_kwargs):
    raise AssertionError("the vision model must not be called on this path")


@pytest.fixture
def vision_env(monkeypatch, tmp_path):
    """Text-only chat model with one image attachment, no DB and no real vision call.

    Other tests drop src.chat_handler from sys.modules, so resolve it here rather
    than at import time — otherwise the patches land on a different module object
    than the one under test.
    """
    module = importlib.import_module("src.chat_handler")
    monkeypatch.setattr(module, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(module, "model_supports_vision", lambda *a, **k: False)
    monkeypatch.setattr(module, "_sync_upload_vision_to_gallery", lambda *a, **k: None)
    monkeypatch.setattr(module, "build_user_content", lambda text, *a, **k: text)
    monkeypatch.setattr(
        importlib.import_module("src.settings"), "get_setting", lambda key, default=None: default
    )
    return SimpleNamespace(module=module, tmp_path=tmp_path, file_info={
        "id": "img-1", "name": "cake.jpg", "path": str(tmp_path / "cake.jpg"),
        "mime": "image/jpeg", "size": 1, "hash": "h",
    })


async def _preprocess(env):
    handler = env.module.ChatHandler(
        session_manager=None, memory_manager=None, chat_processor=None,
        research_handler=None, preset_manager=None,
        upload_handler=_UploadHandler(env.file_info),
    )
    sess = SimpleNamespace(model="chat-model", endpoint_url="", owner="user", id="s1")
    enhanced, *_ = await handler.preprocess_message(
        QUESTION, ["img-1"], sess, auto_opened_docs=[],
    )
    return enhanced


def _describe(env, monkeypatch, text):
    monkeypatch.setattr(env.module, "analyze_image_with_vl_result",
                        lambda *a, **k: {"text": text, "model": "vl-model"})


def _label_above(enhanced, description):
    """The line the description is introduced by."""
    return enhanced.split(description)[0].rstrip("\n").rsplit("\n", 1)[-1]


@pytest.mark.asyncio
async def test_description_is_marked_as_not_the_users_message(vision_env, monkeypatch):
    _describe(vision_env, monkeypatch, DESCRIPTION)
    enhanced = await _preprocess(vision_env)

    assert QUESTION in enhanced, "the user's own words must survive untouched"
    label = _label_above(enhanced, DESCRIPTION)
    assert label.startswith("[") and "user" in label.lower(), (
        "the description is appended as bare prose, so the chat model reads it as "
        f"the user's own writing. Got:\n{enhanced}"
    )


@pytest.mark.asyncio
async def test_cached_description_is_labelled_too(vision_env, monkeypatch):
    """The cache holds an auto description or a hand-corrected one, and the label
    has to be true either way — it is a description of the image regardless."""
    cache = vision_env.tmp_path / ".vision"
    cache.mkdir()
    (cache / "img-1.txt").write_text(DESCRIPTION, encoding="utf-8")
    monkeypatch.setattr(vision_env.module, "analyze_image_with_vl_result", _no_vision_call)

    enhanced = await _preprocess(vision_env)

    label = _label_above(enhanced, DESCRIPTION)
    assert label.startswith("[") and "user" in label.lower(), (
        f"a cached description is injected with no label. Got:\n{enhanced}"
    )


@pytest.mark.asyncio
async def test_client_side_image_marker_is_preserved(vision_env, monkeypatch):
    """chatRenderer.js and sessions.js strip the block by matching `[Image: <name>]\\n`.

    Renaming that marker leaves the raw description rendered in the user's bubble.
    """
    _describe(vision_env, monkeypatch, DESCRIPTION)
    assert "[Image: cake.jpg]\n" in await _preprocess(vision_env)


@pytest.mark.asyncio
async def test_no_dangling_label_when_the_vision_model_returns_nothing(vision_env, monkeypatch):
    _describe(vision_env, monkeypatch, "")
    enhanced = await _preprocess(vision_env)
    assert enhanced.endswith("[Image: cake.jpg]\n"), (
        "an empty description must leave the bare marker, trailing newline included "
        f"— the client's strip regex needs it. Got:\n{enhanced!r}"
    )


@pytest.mark.asyncio
async def test_vision_capable_model_path_is_unchanged(vision_env, monkeypatch):
    monkeypatch.setattr(vision_env.module, "model_supports_vision", lambda *a, **k: True)
    monkeypatch.setattr(vision_env.module, "analyze_image_with_vl_result", _no_vision_call)

    assert await _preprocess(vision_env) == f"{QUESTION}\n\n[Image attached: cake.jpg]"

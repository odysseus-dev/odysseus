"""Regression: strip image content from history for non-vision models (LANE-ODYSSEUS-OPENROUTER-NONVISION-V1).

The Seals session (id fd3e227d-39fb-4ca4-a359-86abd0d2cf09, 2026-06-10)
accumulated an image_url block in its history. The user then sent simple
"?" prompts to ``deepseek/deepseek-v4-pro`` (text-only). Odysseus re-sent
the full conversation including the image, and OpenRouter 404'd with
"No endpoints found that support image input".

The fix is structural: ``_supports_vision(model)`` identifies vision-
capable model name fragments; ``_strip_image_blocks_for_non_vision`` walks
each user message's content and replaces image_url/image blocks with a
short text placeholder. Vision-capable models are passed through
unchanged. Wired into ``llm_call`` / ``llm_call_async`` / ``stream_llm``
right after ``_sanitize_llm_messages``.

These tests pin both helpers.
"""
import pytest

from src.llm_core import _supports_vision, _strip_image_blocks_for_non_vision


# ──────────────────────────────────────────────────────────────────────────
# _supports_vision — vision-capable model detection
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", [
    # The 6/10 failure case — DeepSeek v4 reasoning, no vision.
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-r1",
    "deepseek/deepseek-r1-0528",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-chat-v3.1",
    "deepseek/deepseek-v3.2",
    # Base Qwen3 (text only) — note qwen3-vl is the vision variant.
    "qwen/qwen3-235b-a22b",
    "qwen/qwen3-32b",
    "qwen/qwen3-coder",
    "qwen/qwen3-coder-plus",
    # Base Llama 3.x (text only).
    "meta-llama/llama-3.3-70b-instruct",
    "meta-llama/llama-3.1-70b-instruct",
    # Base Kimi K2 (only the k2-thinking/k2.6 variants are vision).
    "moonshotai/kimi-k2",
    # Non-v Z.AI GLM.
    "z-ai/glm-4.5",
    "z-ai/glm-5",
    # Mistral small/base/mixtral — text only.
    "mistralai/mistral-small-3.1-24b-instruct",
    "mistralai/mixtral-8x22b-instruct",
    # Cohere command — text only.
    "cohere/command-r-plus",
    "cohere/command-a",
    # OpenAI o1-mini specifically has no vision (o1, o1-pro, o3, o3-mini, o3-pro, o4-mini do).
    "openai/o1-mini",
    # Conservative default for unrecognized models.
    "unknown-future-model",
    "fictional/text-only-70b",
    # Empty.
    "",
])
def test_supports_vision_false(model):
    assert _supports_vision(model) is False, f"expected {model!r} to be text-only"


@pytest.mark.parametrize("model", [
    # OpenAI vision.
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1-nano",
    "openai/gpt-4-turbo",
    "openai/gpt-4-vision",
    "openai/gpt-5",
    "openai/gpt-5-mini",
    "openai/gpt-5-pro",
    # OpenAI reasoning-with-vision.
    "openai/o1",
    "openai/o1-pro",
    "openai/o3",
    "openai/o3-mini",
    "openai/o3-pro",
    "openai/o4-mini",
    # Anthropic — all Claude 3+ are vision.
    "anthropic/claude-3-haiku-20240307",
    "anthropic/claude-3-sonnet-20240229",
    "anthropic/claude-3-opus-20240229",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.8",
    "anthropic/claude-haiku-4.5",
    # Google Gemini (1.5+ all vision).
    "google/gemini-1.5-pro",
    "google/gemini-2.0-flash",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.5-flash",
    # Qwen VL family.
    "qwen/qwen-vl-plus",
    "qwen/qwen2.5-vl-72b-instruct",
    "qwen/qwen3-vl-235b-a22b-instruct",
    "qwen/qwen3-vl-8b-instruct",
    # Llama vision.
    "meta-llama/llama-3.2-11b-vision-instruct",
    "meta-llama/llama-3.2-90b-vision-instruct",
    "meta-llama/llama-4-maverick",
    "meta-llama/llama-4-scout",
    # Mistral vision.
    "mistralai/pixtral-12b",
    "mistralai/pixtral-large",
    "mistralai/mistral-medium-3-5",
    # Z.AI vision.
    "z-ai/glm-4.5v",
    "z-ai/glm-4.6v",
    # Gemma 3 has vision.
    "google/gemma-3-27b-it",
    "google/gemma-3-12b-it",
    # Kimi K2 thinking/2.6.
    "moonshotai/kimi-k2-thinking",
    "moonshotai/kimi-k2.6",
    # Amazon Nova.
    "amazon/nova-lite-v1",
    "amazon/nova-pro-v1",
    "amazon/nova-premier-v1",
])
def test_supports_vision_true(model):
    assert _supports_vision(model) is True, f"expected {model!r} to be vision-capable"


# ──────────────────────────────────────────────────────────────────────────
# _strip_image_blocks_for_non_vision
# ──────────────────────────────────────────────────────────────────────────


# The exact shape of the failing Seals session's first image message.
SEALS_IMAGE_MSG = {
    "role": "user",
    "content": [
        {"type": "text", "text": "what to file here manualy"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAACh8AAgkCAYAAACgU/e0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAP+lSURBVHhe7P13nGTned5..."}},
    ],
}

SEALS_HISTORY = [
    {"role": "system", "content": "You are a helpful assistant."},
    SEALS_IMAGE_MSG,
    {"role": "assistant", "content": "I see the Moonshot entry. Let me check its status."},
    {"role": "user", "content": "where exactly? open it infront of me"},
    {"role": "assistant", "content": "I see you're on a page but I can't inspect the image directly."},
    {"role": "user", "content": "?"},
]


def test_strip_replaces_image_with_placeholder_text_deepseek():
    out = _strip_image_blocks_for_non_vision(SEALS_HISTORY, "deepseek/deepseek-v4-pro")
    # System, both assistant messages, the plain user messages all unchanged.
    assert out[0] == {"role": "system", "content": "You are a helpful assistant."}
    assert out[2] == {"role": "assistant", "content": "I see the Moonshot entry. Let me check its status."}
    assert out[3] == {"role": "user", "content": "where exactly? open it infront of me"}
    # The image-bearing user message becomes a single text block with the placeholder.
    img_msg = out[1]
    assert img_msg["role"] == "user"
    assert isinstance(img_msg["content"], list)
    assert len(img_msg["content"]) == 1
    text = img_msg["content"][0]["text"]
    assert "what to file here manualy" in text
    assert "vision support not available for this model" in text
    assert out[5] == {"role": "user", "content": "?"}


def test_strip_image_only_message():
    img_only = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]},
    ]
    out = _strip_image_blocks_for_non_vision(img_only, "deepseek/deepseek-v4-pro")
    assert out[0]["role"] == "user"
    assert isinstance(out[0]["content"], list)
    assert len(out[0]["content"]) == 1
    assert "vision support not available for this model" in out[0]["content"][0]["text"]


def test_strip_handles_anthropic_image_block_type():
    """Defensive: Anthropic-style 'image' block type (in addition to 'image_url')."""
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
        ]},
    ]
    out = _strip_image_blocks_for_non_vision(msgs, "deepseek/deepseek-v4-pro")
    text = out[0]["content"][0]["text"]
    assert "look" in text
    assert "vision support not available" in text
    # No image block survives.
    assert not any(b.get("type") == "image" for b in out[0]["content"])


def test_strip_no_op_when_no_images():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    out = _strip_image_blocks_for_non_vision(msgs, "deepseek/deepseek-v4-pro")
    assert out == msgs  # exact same objects back


def test_strip_passthrough_for_vision_model():
    """Vision models must not lose their images."""
    msgs = [dict(SEALS_IMAGE_MSG)]
    out = _strip_image_blocks_for_non_vision(msgs, "openai/gpt-4o")
    # Same object, unchanged.
    assert out == msgs
    # And specifically the image_url block survives.
    assert any(b.get("type") == "image_url" for b in out[0]["content"])


def test_strip_does_not_touch_assistant_role():
    """Defensive: only user-role messages are touched. Assistant tool-call
    images (rare but possible) are left alone — providers handle them
    and stripping could break valid flows."""
    msgs = [
        {"role": "user", "content": "look"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I see"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}},
        ]},
    ]
    out = _strip_image_blocks_for_non_vision(msgs, "deepseek/deepseek-v4-pro")
    # Assistant image_url survives.
    assert any(b.get("type") == "image_url" for b in out[1]["content"])


def test_strip_does_not_mutate_input():
    msgs = [dict(SEALS_IMAGE_MSG)]
    msgs[0]["content"] = list(msgs[0]["content"])  # fresh list
    snapshot = list(msgs[0]["content"])
    _strip_image_blocks_for_non_vision(msgs, "deepseek/deepseek-v4-pro")
    assert msgs[0]["content"] == snapshot


def test_strip_tolerates_none_string_and_non_dict_messages():
    messy = [
        None,
        {"role": "user", "content": "hi"},
        "string-not-dict",
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
    ]
    out = _strip_image_blocks_for_non_vision(messy, "deepseek/deepseek-v4-pro")
    assert out[0] is None
    assert out[1] == {"role": "user", "content": "hi"}
    assert out[2] == "string-not-dict"
    # The image-bearing message still gets stripped.
    assert "vision support not available" in out[3]["content"][0]["text"]


def test_strip_with_empty_messages():
    assert _strip_image_blocks_for_non_vision([], "deepseek/deepseek-v4-pro") == []
    assert _strip_image_blocks_for_non_vision(None, "deepseek/deepseek-v4-pro") == []  # type: ignore[arg-type]

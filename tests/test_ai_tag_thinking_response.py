"""Regression coverage for gallery ai-tag's thinking-model response handling.

Before this fix, the non-Anthropic branch read only choices[0].message.content
and committed the result as tags — a thinking-capable model with an empty
content and the real answer in reasoning_content (or Mistral's structured
content list) silently produced ai_tags="" reported as a success, and any
raw thinking-wrapper text that did land in content was persisted verbatim
as a "tag". See PR #5965 review.
"""
from routes.gallery.gallery_routes import _extract_ai_tag_response_text


def test_anthropic_reads_content_block():
    data = {"content": [{"text": "cat, sofa, indoor"}]}
    assert _extract_ai_tag_response_text(data, "anthropic") == "cat, sofa, indoor"


def test_openai_reads_plain_content():
    data = {"choices": [{"message": {"content": "dog, park, outdoor"}}]}
    assert _extract_ai_tag_response_text(data, "openai") == "dog, park, outdoor"


def test_empty_content_falls_back_to_reasoning_content():
    data = {"choices": [{"message": {"content": "", "reasoning_content": "bike, street, day"}}]}
    assert _extract_ai_tag_response_text(data, "openai") == "bike, street, day"


def test_missing_content_key_falls_back_to_reasoning_content():
    data = {"choices": [{"message": {"reasoning_content": "boat, lake, sunset"}}]}
    assert _extract_ai_tag_response_text(data, "openai") == "boat, lake, sunset"


def test_mistral_structured_content_extracts_text_part():
    data = {
        "choices": [{
            "message": {
                "content": [
                    {"type": "text", "text": "car, garage, night"},
                ],
            },
        }],
    }
    result = _extract_ai_tag_response_text(data, "openai")
    assert "car, garage, night" in result


def test_thinking_markup_is_stripped_before_tag_split():
    data = {
        "choices": [{
            "message": {
                "content": "<think>Let me look at the image carefully.</think>tree, forest, green",
            },
        }],
    }
    result = _extract_ai_tag_response_text(data, "openai")
    assert "think" not in result.lower()
    assert "tree, forest, green" in result


def test_gemma_channel_thinking_wrapper_is_stripped():
    data = {
        "choices": [{
            "message": {
                "content": "<|channel>thought\nreasoning about the photo\n<channel|>chair, desk, office",
            },
        }],
    }
    result = _extract_ai_tag_response_text(data, "openai")
    assert "chair, desk, office" in result
    assert "channel" not in result.lower()


def test_thinking_only_response_yields_empty_after_strip():
    # Model spent its whole budget "thinking" and never produced a visible
    # answer — the route is expected to treat this as empty and error out
    # rather than commit an empty ai_tags string as a reported success.
    data = {
        "choices": [{
            "message": {
                "content": "<think>Still analyzing the composition and lighting...</think>",
            },
        }],
    }
    result = _extract_ai_tag_response_text(data, "openai")
    assert result.strip() == ""

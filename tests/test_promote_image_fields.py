"""Unit tests for `_promote_image_fields` (PR #2809).

`generate_image` is a text-only MCP tool, so the saved image URL never reaches
the agent loop's structured forwarding (which renders the image via
`buildImageBubble` on `result["image_url"]`). `_promote_image_fields` lifts the
URL — plus prompt/model/size — out of the tool's stdout into structured fields so
the image renders deterministically, without relying on the model echoing the URL
into prose. These cases cover the absolute-URL, relative-URL, no-URL, and
non-success-exit paths.
"""
from src.tool_execution import _promote_image_fields


def _result(stdout, exit_code=0):
    return {"exit_code": exit_code, "stdout": stdout}


def test_absolute_url_promoted_with_fields():
    """An absolute https URL in stdout is lifted into image_url, along with the
    prompt/model/size lines."""
    r = _result(
        "Generated image for: a red fox in snow\n"
        "Direct link: https://odysseus.example.com/api/generated-image/abc123.png\n"
        "model: qwen-image\n"
        "size: 1024x1024"
    )
    _promote_image_fields(r)
    assert r["image_url"] == "https://odysseus.example.com/api/generated-image/abc123.png"
    assert r["image_prompt"] == "a red fox in snow"
    assert r["image_model"] == "qwen-image"
    assert r["image_size"] == "1024x1024"


def test_relative_url_promoted():
    """A relative /api/generated-image/... path (no host) is still matched."""
    r = _result(
        "Generated image for: a cat\n"
        "Direct link: /api/generated-image/def456.png"
    )
    _promote_image_fields(r)
    assert r["image_url"] == "/api/generated-image/def456.png"
    assert r["image_prompt"] == "a cat"


def test_no_url_leaves_result_unchanged():
    """No generated-image URL anywhere -> no image_url key is added."""
    r = _result("Generated image for: a dog\n(no link produced)")
    _promote_image_fields(r)
    assert "image_url" not in r
    assert "image_prompt" not in r


def test_nonzero_exit_not_promoted():
    """A non-success result is never promoted, even if stdout contains a URL."""
    r = _result("https://host/api/generated-image/zzz.png", exit_code=1)
    _promote_image_fields(r)
    assert "image_url" not in r

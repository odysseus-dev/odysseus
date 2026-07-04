"""Regression: OpenAI->Anthropic image conversion must emit a valid media_type.

A .jpg attachment produces a `data:image/jpg;base64,...` data-URI (see
build_user_content), and _convert_openai_content_to_anthropic took the
media_type verbatim from the header. Anthropic's image API only accepts
`image/jpeg` (and png/gif/webp) — `image/jpg` is rejected, so JPEG images
failed to reach Anthropic-family models. The alias is now normalized; other
media types pass through unchanged.
"""
from src.llm_core import _convert_openai_content_to_anthropic


def _img(url):
    return [{"type": "image_url", "image_url": {"url": url}}]


def test_jpg_alias_normalized_to_jpeg():
    out = _convert_openai_content_to_anthropic(_img("data:image/jpg;base64,QUJD"))
    assert out[0]["source"]["media_type"] == "image/jpeg"
    assert out[0]["source"]["data"] == "QUJD"


def test_png_media_type_unchanged():
    out = _convert_openai_content_to_anthropic(_img("data:image/png;base64,QUJD"))
    assert out[0]["source"]["media_type"] == "image/png"


def test_jpeg_media_type_unchanged():
    out = _convert_openai_content_to_anthropic(_img("data:image/jpeg;base64,QUJD"))
    assert out[0]["source"]["media_type"] == "image/jpeg"

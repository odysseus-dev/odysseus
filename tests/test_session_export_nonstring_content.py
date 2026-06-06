"""Regression: session export must tolerate non-string message content.

A message's ``content`` is a plain string for normal turns, but a multimodal
list of content blocks for image/vision turns, and ``None`` for assistant turns
that persisted only native tool_calls. The txt/html/md exporters in
``routes/session_routes.py`` joined and string-munged ``content`` directly, so:

  - txt:  ``"\n".join([..., <list>, ...])``      -> TypeError
  - html: ``<list>.replace("&", "&amp;")``        -> AttributeError
  - md:   ``f"{<list>}"``                          -> raw Python repr in output

``_content_to_text`` coerces all three shapes to plain text so export degrades
gracefully instead of returning a 500.
"""
from routes.session_routes import _content_to_text


def test_plain_string_passes_through_unchanged():
    assert _content_to_text("hello world") == "hello world"
    assert _content_to_text("") == ""


def test_multimodal_list_flattens_to_its_text_blocks():
    content = [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "thanks"},
    ]
    assert _content_to_text(content) == "describe this\nthanks"


def test_none_content_becomes_empty_string():
    # Assistant turns carrying only native tool_calls persist content as None.
    assert _content_to_text(None) == ""


def test_list_without_text_blocks_is_empty_not_crash():
    assert _content_to_text([{"type": "image_url", "image_url": {"url": "x"}}]) == ""
    assert _content_to_text([]) == ""


def test_coerced_output_survives_the_export_operations():
    # The exact operations that previously crashed must now succeed.
    history = ["plain", [{"type": "text", "text": "img turn"}], None]
    texts = [_content_to_text(c) for c in history]
    # txt export path
    assert "\n".join(texts) == "plain\nimg turn\n"
    # html export path
    for t in texts:
        assert isinstance(t.replace("&", "&amp;"), str)

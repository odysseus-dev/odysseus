"""Generated videos play inline in chat.

The markdown renderer knew images and audio only, so
`![clip](/api/generated-image/X.mp4)` rendered as a broken <img>, and the
linked shape `[![clip](X.mp4)](X.mp4)` showed the escaped <img> markup as text.
An uploaded video attachment showed only as a download card. Only this
origin's generated-media route is played inline; any other video URL keeps its
previous image/link rendering.
"""

import re
from pathlib import Path

from tests.test_markdown_rendering_js import _run_markdown_case, node_available  # noqa: F401

_REPO = Path(__file__).resolve().parent.parent
_VIDEO = re.compile(
    r'^<video class="chat-video" src="http://localhost/api/generated-image/c1\.(mp4|webm)"'
    r'[^<>]* controls preload="metadata" playsinline></video>$'
)


def test_image_embed_of_generated_video_is_a_video(node_available):
    assert _VIDEO.match(_run_markdown_case("![clip](/api/generated-image/c1.mp4)"))


def test_linked_video_embed_is_one_video_not_escaped_markup(node_available):
    html = _run_markdown_case("[![clip](/api/generated-image/c1.mp4)](/api/generated-image/c1.mp4)")
    assert _VIDEO.match(html)
    assert "&lt;" not in html


def test_plain_link_to_generated_video_is_a_video(node_available):
    html = _run_markdown_case("see [the clip](/api/generated-image/c1.webm) now")
    assert 'title="the clip"' in html
    assert _VIDEO.match(html.removeprefix("<p>see ").removesuffix(" now</p>"))


def test_generated_images_still_render_as_img(node_available):
    assert _run_markdown_case("![cat](/api/generated-image/c1.png)").startswith("<img ")


def test_foreign_video_urls_are_not_played(node_available):
    link = _run_markdown_case("[x](https://evil.example/api/generated-image/c1.mp4)")
    assert link.startswith("<a ") and "<video" not in link
    img = _run_markdown_case("![x](https://evil.example/a.mp4)")
    assert img.startswith("<img ") and "<video" not in img


def test_video_label_is_escaped(node_available):
    html = _run_markdown_case('[a"><b>x](/api/generated-image/c1.mp4)')
    assert "<b>" not in html
    assert "&quot;" in html


def test_csp_allows_same_origin_media():
    src = (_REPO / "core" / "middleware.py").read_text(encoding="utf-8")
    assert "media-src 'self'" in src


# Uploaded video attachments. chatRenderer.js pulls in too much of the app to
# run in node, so these pin the source.
_RENDERER = (_REPO / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")


def _video_branch():
    start = _RENDERER.index("An uploaded video plays in the bubble")
    return _RENDERER[_RENDERER.rindex("} else if", 0, start):_RENDERER.index("// Non-image file card", start)]


def test_uploaded_video_attachment_gets_a_player():
    branch = _video_branch()
    assert "att.id &&" in branch  # never for an optimistic, id-less bubble
    assert "video/" in branch and "mp4|webm|mov|m4v" in branch
    assert "document.createElement('video')" in branch
    assert "`/api/upload/${att.id}`" in branch
    assert "vid.controls = true" in branch and "vid.playsInline = true" in branch
    assert "textContent = att.name" in branch  # name via textContent, never innerHTML
    assert "innerHTML" not in branch


def test_video_branch_sits_between_image_preview_and_file_card():
    img = _RENDERER.index("attachWrap.appendChild(imgWrap);")
    vid = _RENDERER.index("An uploaded video plays in the bubble")
    card = _RENDERER.index("// Non-image file card")
    assert img < vid < card

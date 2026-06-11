"""Regression guards for agent-tool screenshot DOM sinks."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def test_live_tool_screenshot_does_not_template_raw_sse_value():
    chat = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")

    assert "safeToolScreenshotSrc(json.screenshot)" in chat
    assert 'img.src = screenshotSrc' in chat
    assert 'details.innerHTML = `<summary>Screenshot</summary><img src="${json.screenshot}"' not in chat


def test_restored_tool_screenshot_uses_raster_data_url_whitelist():
    renderer = (_REPO / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")

    assert "export function safeToolScreenshotSrc(raw)" in renderer
    assert "(?:png|jpe?g|gif|webp)" in renderer
    assert "safeToolScreenshotSrc(ev.screenshot)" in renderer
    assert 'src="${esc(ev.screenshot)}"' not in renderer


def test_streaming_tool_labels_are_escaped_before_inner_html():
    chat = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    compare = (_REPO / "static" / "js" / "compare" / "stream.js").read_text(encoding="utf-8")

    assert '<span class="agent-thread-tool">${esc(toolLabel)}</span>' in chat
    assert '<span class="agent-thread-tool">${toolLabel}</span>' not in chat
    assert '<span class="agent-thread-tool">${escapeHtml(toolLabel)}</span>' in compare
    assert '<span class="agent-thread-tool">${toolLabel}</span>' not in compare


def test_generated_image_urls_are_vetted_before_assignment_or_open():
    renderer = (_REPO / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
    compare = (_REPO / "static" / "js" / "compare" / "stream.js").read_text(encoding="utf-8")
    group = (_REPO / "static" / "js" / "group.js").read_text(encoding="utf-8")

    assert "export function safeDisplayImageSrc(raw)" in renderer
    assert "safeDisplayImageSrc(imageUrl)" in renderer
    assert "img.src = safeImageUrl" in renderer
    assert "window.open(safeImageUrl, '_blank', 'noopener,noreferrer')" in renderer
    assert "safeDisplayImageSrc," in renderer
    assert "safeDisplayImageSrc(json.image_url)" in compare
    assert "img.src = json.image_url" not in compare
    assert "chatRenderer.safeDisplayImageSrc(json.url)" in group
    assert "img.src = json.url" not in group


def test_group_chat_role_labels_are_escaped_before_inner_html():
    group = (_REPO / "static" / "js" / "group.js").read_text(encoding="utf-8")

    assert '<div class="role">${uiModule.esc(roleLabel)}' in group
    assert '<div class="role">${roleLabel}' not in group


def test_main_chat_role_labels_are_escaped_before_inner_html():
    chat = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")

    assert '<div class="role">${uiModule.esc(roleLabel)}' in chat
    assert "'<div class=\"role\">' + uiModule.esc(roleLabel)" in chat
    assert '<div class="role">${uiModule.esc(agentModelLabel)}' in chat
    assert '<div class="role">${roleLabel}' not in chat
    assert "'<div class=\"role\">' + roleLabel" not in chat
    assert '<div class="role">${agentModelLabel}' not in chat


def test_compare_search_result_links_are_http_only():
    compare = (_REPO / "static" / "js" / "compare" / "stream.js").read_text(encoding="utf-8")

    assert "function _safeHttpHref(raw)" in compare
    assert "const safeUrl = _safeHttpHref(r.url);" in compare
    assert "titleLink.href = safeUrl;" in compare
    assert "titleLink.href = r.url || '#';" not in compare


def test_compare_probe_provider_labels_are_escaped():
    selector = (_REPO / "static" / "js" / "compare" / "selector.js").read_text(encoding="utf-8")

    assert "${escapeHtml(p.label || p.id)}" in selector
    assert "${p.label || p.id}" not in selector

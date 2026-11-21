"""Notes Markdown sanitization and mounted card / note window interactions in Chromium."""
import base64
import os
from pathlib import Path
import re

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://notes.test"
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


@pytest.fixture
def page():
    if sync_playwright is None:
        pytest.fail("playwright is required for notes browser tests. Install requirements-dev.txt")
    with sync_playwright() as playwright:
        launch_kwargs = {"headless": True, "args": ["--disable-dev-shm-usage", "--no-sandbox"]}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
            launch_kwargs["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"]
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page()
        foreign = []

        def serve(route):
            url = route.request.url
            if not url.startswith(ORIGIN + "/"):
                foreign.append(url)
                route.abort()
                return
            relative = url[len(ORIGIN) + 1:].split("?", 1)[0]
            if not relative:
                route.fulfill(body="<html><body></body></html>", content_type="text/html")
            elif relative.startswith("api/"):
                route.fulfill(status=404, body="{}", content_type="application/json")
            elif (ROOT / relative).is_file():
                route.fulfill(path=str(ROOT / relative))
            else:
                route.fulfill(status=404, body="Not found")

        page.route("**/*", serve)
        page.goto(ORIGIN + "/")
        page.foreign_requests = foreign
        yield page
        browser.close()


RENDER = """async (source) => {
  const {mdToHtml} = await import('/static/js/markdown.js');
  return mdToHtml(source, {tasks: 'interactive', images: 'notes'});
}"""


def test_notes_scrub_strips_cross_origin_fetches_and_loads_nothing(page):
    source = "\n".join([
        "<details><summary>s</summary>",
        '<img srcset="https://evil.example/a.png 1x" src="/api/upload/ok1">',
        '<video poster="https://evil.example/p.png"></video>',
        '<div style="background-image: url(//evil.example/b.png)">bg</div>',
        '<table background="https://evil.example/t.png"><tr><td>x</td></tr></table>',
        '<img src="https://evil.example/c.png">',
        "</details>",
        "",
        "after",
    ])
    html = page.evaluate(RENDER, source)
    assert "evil.example" not in html, html
    assert 'src="/api/upload/ok1"' in html, html
    for attr in ("srcset=", "poster=", "background=", "url("):
        assert attr not in html, (attr, html)
    assert "<details>" in html and "after" in html, html
    page.wait_for_timeout(300)
    assert page.foreign_requests == []


def test_notes_scrub_keeps_structure_when_nothing_fetches(page):
    """The scrub only removes attributes, inside the allowed-HTML sanitizer:
    a note with nothing to strip renders exactly as with the scrub off."""
    source = "\n".join([
        "# Title", "", "- [ ] task **bold**", "- [x] done", "",
        "| a | b |", "|---|---|", "| 1 | 2 |", "",
        "<details><summary>more</summary>", "inner [link](https://example.org)", "</details>", "",
        "```js", "const x = '<b>' + 1;", "```", "", "> quote with `code` & < >",
    ])
    scrubbed, plain = page.evaluate("""async (source) => {
      const {mdToHtml} = await import('/static/js/markdown.js');
      return [mdToHtml(source, {tasks: 'interactive', images: 'notes'}), mdToHtml(source, {tasks: 'interactive'})];
    }""", source)
    assert scrubbed == plain


def _mount_notes(page):
    shell = (ROOT / "static/index.html").read_text(encoding="utf-8")
    styles = "\n".join(re.findall(r'<link[^>]+rel="stylesheet"[^>]*>', shell))
    page.set_content(
        f"<head>{styles}</head>"
        '<div id="chat-container"></div><div id="toast"></div>'
        '<button id="tool-notes-btn"></button><button id="rail-notes"></button>'
    )
    page.evaluate("""async () => {
      localStorage.setItem('odysseus-notes-first-open-hint-v1', '1');
      const notes = await import('/static/js/notes.js');
      notes.openPanel();
    }""")


def test_notes_cards_mount_images_locally_and_editor_sends_full_document(page):
    page.set_default_timeout(8000)
    upload = "/api/upload/pixel.png"
    raster = f"data:image/png;base64,{PIXEL}"
    foreign = "https://images.example.test/photo.png"
    source = (
        f"![upload]({upload})\n\n![raster]({raster})\n\n![external]({foreign})\n\n"
        '<details><summary>Details</summary>'
        '<div style="background-image: image-set(\'//images.example.test/bg.png\' 1x)">body</div>'
        '<img srcset="//images.example.test/large.png 2x"><video poster="//images.example.test/poster.png"></video>'
        "</details>"
    )
    notes = [
        {"id": "plain", "title": "Photo note", "content": source, "note_type": "note", "body_revision": "r1"},
        {"id": "draw", "title": "Drawing", "content": f"![]({foreign})", "note_type": "draw", "image_url": foreign},
        {"id": "canvas", "title": "Local drawing", "content": f"![]({upload})", "note_type": "draw", "image_url": upload},
    ]
    writes = []

    def note_api(route):
        if route.request.method == "PUT":
            writes.append(route.request.post_data_json)
            route.fulfill(json={**notes[0], **writes[-1], "body_revision": "r2"})
        else:
            route.fulfill(json={"notes": notes})

    page.route(f"{ORIGIN}/api/notes**", note_api)
    page.route(f"{ORIGIN}{upload}", lambda route: route.fulfill(body=base64.b64decode(PIXEL), content_type="image/png"))
    _mount_notes(page)
    card = page.locator('.note-card[data-note-id="plain"]')
    card.locator('img[alt="upload"]').wait_for()
    page.wait_for_function("""() => ['upload', 'raster'].every(alt =>
      document.querySelector(`.note-card img[alt="${alt}"]`)?.naturalWidth === 1)""")
    card.locator("details summary").click()
    assert card.get_by_role("link", name="external").get_attribute("href") == foreign
    assert page.locator('.note-card[data-note-id="draw"] img').count() == 0
    assert page.foreign_requests == []

    # The note window saves the whole document, against the revision it saw.
    card.locator('.note-card-title').click()
    _window_body(page).wait_for()
    page.locator('#note-window-modal .note-window-toolbar [data-md="source"]').click()
    page.locator('#note-window-modal .note-window-source').fill("Replacement without images")
    page.wait_for_function("() => document.querySelector('.note-card[data-note-id=\"plain\"]') && !document.querySelector('.note-card[data-note-id=\"plain\"] img')")
    assert len(writes) == 1
    assert writes[0]["body_revision"] == "r1"
    assert writes[0]["replace_content"] is True
    assert writes[0]["content"] == "Replacement without images"
    _close_window(page)
    # A drawing opens in the window with its image; nothing loads from elsewhere.
    page.locator('.note-card[data-note-id="canvas"] .note-card-title').click()
    page.locator('#note-window-modal .note-window-body img.md-note-image').wait_for(state="attached")
    assert page.foreign_requests == []


def _window_body(page):
    return page.locator('#note-window-modal .note-window-body')


def _replace_body(page, text):
    """Select the whole body and type `text` over it, as a user would."""
    body = _window_body(page)
    body.wait_for()
    body.click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type(text)


def _close_window(page):
    page.evaluate("() => document.querySelector('#note-window-modal .modal-close').click()")
    page.locator('#note-window-modal').wait_for(state="detached")


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "mobile"])
def test_edit_typed_during_save_restores_with_new_revision(page, mobile):
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.ontouchstart = null")
    note = {"id": "draft", "title": "Draft test", "note_type": "note",
            "content": "Original text", "body_revision": "r1"}
    pending = []

    def note_api(route):
        if route.request.method != "PUT":
            route.fulfill(json={"notes": [note]})
        elif note["body_revision"] == "r1":
            pending.append(route)
        else:
            note.update(content=route.request.post_data_json["content"], body_revision="r3")
            route.fulfill(json=note)

    page.route(f"{ORIGIN}/api/notes**", note_api)
    _mount_notes(page)
    page.locator('.note-card[data-note-id="draft"] .note-card-title').click()
    # The autosave of the first text is held at the server while more is typed.
    with page.expect_request(lambda request: request.method == "PUT"):
        _replace_body(page, "Submitted text")
    page.keyboard.press("End")
    page.keyboard.type(", then a later edit")
    assert len(pending) == 1
    payload = pending[0].request.post_data_json
    assert payload["content"] == "Submitted text" and payload["body_revision"] == "r1"
    assert payload["replace_content"] is True
    draft = page.evaluate("() => JSON.parse(localStorage.getItem('odysseus-note-draft-draft'))")
    assert draft["content"] == "Submitted text, then a later edit"
    # The first save lands at r2: the later text goes next, against r2.
    with page.expect_request(lambda request: request.method == "PUT") as saved:
        note.update(content=payload["content"], body_revision="r2")
        pending.pop().fulfill(json=note)
    assert saved.value.post_data_json["content"] == "Submitted text, then a later edit"
    assert saved.value.post_data_json["body_revision"] == "r2"
    page.wait_for_function("() => !localStorage.getItem('odysseus-note-draft-draft')")
    _close_window(page)


def test_stale_drafts_are_copyable_without_being_applied(page):
    note = {"id": "draft", "title": "Draft test", "note_type": "note",
            "content": "Server text", "body_revision": "r2"}
    writes = []

    def note_api(route):
        if route.request.method == "PUT":
            writes.append(route.request.post_data_json)
            note.update(content=writes[-1]["content"], body_revision="r3")
            route.fulfill(json=note)
        else:
            route.fulfill(json={"notes": [note]})

    page.route(f"{ORIGIN}/api/notes**", note_api)
    page.evaluate("""() => localStorage.setItem('odysseus-note-draft-draft',
      JSON.stringify({content: 'Stale edit', body_revision: 'r1'}))""")
    _mount_notes(page)
    # This test page is not a secure context: give it a clipboard to read back.
    page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {
      configurable: true, value: { writeText: async text => { window.copiedDraft = text; } } })""")
    title = page.locator('.note-card[data-note-id="draft"] .note-card-title')
    title.click()
    body = _window_body(page)
    body.wait_for()
    assert body.inner_text() == "Server text", "a stale draft is never laid over the newer note"
    banner = page.locator('#note-window-modal .note-window-banner')
    banner.get_by_role("button", name="Copy draft").click()
    page.wait_for_function("() => window.copiedDraft !== undefined")
    assert "Stale edit" in page.evaluate("() => window.copiedDraft")
    assert banner.get_by_role("button", name="Restore").count() == 0
    assert writes == []

    # Typing on the current revision saves against it, and the stale draft is
    # still there to copy from the next time the note opens.
    _replace_body(page, "Chosen edit on current revision")
    page.wait_for_function("() => !localStorage.getItem('odysseus-note-draft-draft')")
    assert writes[0]["body_revision"] == "r2"
    assert writes[-1]["content"] == "Chosen edit on current revision"
    _close_window(page)
    title.click()
    assert body.inner_text() == "Chosen edit on current revision"
    page.evaluate("() => { window.copiedDraft = undefined; }")
    banner.get_by_role("button", name="Copy draft").click()
    page.wait_for_function("() => window.copiedDraft !== undefined")
    assert "Stale edit" in page.evaluate("() => window.copiedDraft")


def test_task_link_navigates_and_checkbox_still_toggles(page):
    text = "Read the [guide](#guide)"
    note = {"id": "task", "title": "Task with a link", "note_type": "checklist",
            "body_revision": "r0", "content": f"- [ ] {text}",
            "items": [{"index": 0, "text": text, "done": False}]}
    toggles = []

    def note_api(route):
        if route.request.method == "POST":
            toggles.append(route.request.post_data_json)
            done = len(toggles) % 2 == 1
            note.update(
                content=f"- [{'x' if done else ' '}] {text}",
                items=[{"index": 0, "text": text, "done": done}],
                body_revision=f"r{len(toggles)}",
            )
            route.fulfill(json={"note": note})
        else:
            route.fulfill(json={"notes": [note]})

    page.route(f"{ORIGIN}/api/notes**", note_api)
    _mount_notes(page)
    card = page.locator('.note-card[data-note-id="task"]')
    card.get_by_role("link", name="guide").click()
    page.wait_for_url(f"{ORIGIN}/#guide", timeout=2000)
    assert toggles == []

    checkbox = card.get_by_role("checkbox")
    with page.expect_response("**/items/0/toggle"):
        checkbox.click()
    checkbox.and_(card.locator('[aria-checked="true"]')).wait_for()
    with page.expect_response("**/items/0/toggle"):
        checkbox.press("Space")
    checkbox.and_(card.locator('[aria-checked="false"]')).wait_for()
    assert toggles == [{"body_revision": "r0"}, {"body_revision": "r1"}]

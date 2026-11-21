"""Notes Markdown sanitization and mounted card/editor interactions in Chromium."""
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

    card.locator('.note-card-title').click()
    page.locator('.note-form-content').fill("Replacement without images")
    page.locator('.note-form-save').click()
    page.locator('.note-form').wait_for(state="detached")
    assert len(writes) == 1
    assert writes[0]["body_revision"] == "r1"
    assert writes[0]["replace_content"] is True
    assert writes[0]["content"] == "Replacement without images"
    assert card.locator('img').count() == 0
    canvas_card = page.locator('.note-card[data-note-id="canvas"]')
    canvas_card.locator('.note-card-image').click()
    page.locator('.note-form-canvas').wait_for()
    assert page.foreign_requests == []


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
    title = page.locator('.note-card[data-note-id="draft"] .note-card-title')
    title.click()
    editor = page.locator('.note-form-content')
    if mobile:
        page.locator('.note-form-content-reader').click()
    editor.fill("Submitted text")
    with page.expect_request(lambda request: request.method == "PUT"):
        page.locator('.note-form-save').click()
    editor.fill("Later edit")
    assert len(pending) == 1
    payload = pending[0].request.post_data_json
    assert payload["content"] == "Submitted text" and payload["body_revision"] == "r1"
    note.update(content=payload["content"], body_revision="r2")
    pending.pop().fulfill(json=note)
    editor.wait_for(state="detached")

    title.click()
    if mobile:
        reader = page.locator('.note-form-content-reader')
        assert reader.inner_text() == "Later edit"
        reader.click()
    assert editor.input_value() == "Later edit"
    if mobile:
        # Use the editor shortcut so the untouched mobile form does not archive.
        editor.fill("Later edit!")
    with page.expect_request(lambda request: request.method == "PUT") as saved:
        page.locator('.note-form-save').click()
    assert saved.value.post_data_json["content"] == ("Later edit!" if mobile else "Later edit")
    assert saved.value.post_data_json["body_revision"] == "r2"
    editor.wait_for(state="detached")


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
    title = page.locator('.note-card[data-note-id="draft"] .note-card-title')
    title.click()
    editor = page.locator('.note-form-content')
    assert editor.input_value() == "Server text"
    page.get_by_text("Recover unsaved draft").click()
    recovery = page.get_by_role("textbox", name="Unsaved draft")
    assert recovery.input_value() == "Stale edit"
    assert recovery.get_attribute("readonly") is not None
    assert writes == []

    editor.fill("Chosen edit on current revision")
    page.locator('.note-form-save').click()
    editor.wait_for(state="detached")
    assert writes[0]["body_revision"] == "r2"
    assert writes[-1]["content"] == "Chosen edit on current revision"
    title.click()
    assert editor.input_value() == "Chosen edit on current revision"
    page.get_by_text("Recover unsaved draft").click()
    assert recovery.input_value() == "Stale edit"


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

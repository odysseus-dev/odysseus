"""Notes markdown in a real browser: preserved HTML cannot make the page fetch
another origin (srcset, poster, background, CSS url(), a foreign src), and
rendering loads nothing.

The Node harness (tests/streaming/markdownHarness.mjs) has no DOM, so the
allowed-HTML sanitizer walks nothing there; this runs `mdToHtml` from
static/js/markdown.js in Chromium instead.
"""
import os
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://notes.test"


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

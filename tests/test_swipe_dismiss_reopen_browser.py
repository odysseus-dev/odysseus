"""Regression for #3850: a swiped-away tool must reopen on the first tap.

On touch devices ui.js dismisses a sheet by adding `.hidden` to the modal and
dispatching `modal-dismissed`. Tasks, Gallery and Research keep their own
`_open` flag, so if they ignore that event the sidebar toggle still thinks the
tool is open: the next tap runs close() and only the second tap reopens it.

Runs the real static/js modules in headless Chromium with the API stubbed.
"""
import os
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://swipe.test'

FETCH_STUB = """() => {
  window.fetch = async () => new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}});
}"""

# (modal id, the click handler static/app.js wires to the tool's sidebar button)
TOOLS = [
    ('tasks-modal', """async () => { const m = await import('/static/js/tasks.js');
        m.isTasksOpen() ? m.closeTasks() : m.openTasks(); }"""),
    ('gallery-modal', """async () => { const m = await import('/static/js/gallery.js');
        const Modals = await import('/static/js/modalManager.js');
        if (!Modals.toggle('gallery-modal')) {
          if (m.isGalleryOpen()) m.closeGallery(); else m.openGallery(); } }"""),
    ('research-overlay', """async () => { const m = await import('/static/js/research/panel.js');
        m.toggle(); }"""),
]

SWIPE_THEN_ONE_TAP = """async ([id, tapSrc]) => {
  const tap = (0, eval)('(' + tapSrc + ')');
  const visible = () => {
    const el = document.getElementById(id);
    return !!el && !el.classList.contains('hidden') && getComputedStyle(el).display !== 'none';
  };
  await tap();
  await new Promise(r => setTimeout(r, 300));
  const openedFirst = visible();
  // What ui.js does at the end of a swipe-down dismiss.
  const modal = document.getElementById(id);
  modal.classList.add('hidden');
  modal.style.display = '';
  window.dispatchEvent(new CustomEvent('modal-dismissed', { detail: { id } }));
  await new Promise(r => setTimeout(r, 400));
  const hiddenAfterSwipe = !visible();
  await tap();
  await new Promise(r => setTimeout(r, 300));
  return { openedFirst, hiddenAfterSwipe, visibleAfterOneTap: visible() };
}"""


@pytest.fixture
def page():
    if sync_playwright is None:
        pytest.fail('playwright is required for browser tests. Install requirements-dev.txt')
    with sync_playwright() as playwright:
        launch_kwargs = {'headless': True, 'args': ['--disable-dev-shm-usage', '--no-sandbox']}
        if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'):
            launch_kwargs['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH']
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)

        def serve(route):
            relative = route.request.url.split(ORIGIN + '/', 1)[1].split('?', 1)[0]
            if not relative:
                route.fulfill(body='<html><body><div id="chat-container"></div></body></html>',
                              content_type='text/html')
            elif (ROOT / relative).is_file():
                route.fulfill(path=str(ROOT / relative))
            else:
                route.fulfill(status=404, body='Not found')

        page.route(ORIGIN + '/**', serve)
        page.goto(ORIGIN + '/')
        page.evaluate(FETCH_STUB)
        yield page
        browser.close()


@pytest.mark.parametrize('tool', TOOLS, ids=[t[0] for t in TOOLS])
def test_swiped_away_tool_reopens_on_first_tap(page, tool):
    result = page.evaluate(SWIPE_THEN_ONE_TAP, list(tool))
    assert result['openedFirst'], result
    assert result['hiddenAfterSwipe'], result
    assert result['visibleAfterOneTap'], f'{tool[0]} needed a second tap after swipe-dismiss: {result}'

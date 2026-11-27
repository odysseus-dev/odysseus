"""Regression for #3575: the skill card's kebab-menu Edit must never save.

Brain > Skills: choosing Edit from the three-dot menu while the SKILL.md editor
is already open used to run the Save path (POST /api/skills/<name>/markdown),
silently committing half-finished edits. The menu entry now only enters (or
focuses) edit mode; the footer Save button stays the only save trigger.

Runs the real static/js/skills.js in headless Chromium with the API stubbed.
"""
import os
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://skills.test'

FETCH_STUB = """() => {
  window.__posts = [];
  const json = (body) => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = (opts.method || 'GET').toUpperCase();
    if (method !== 'GET' && u.includes('/api/skills')) window.__posts.push(method + ' ' + u.replace(location.origin, ''));
    if (u.endsWith('/api/skills')) return json({skills: [{name: 'demo-skill', description: 'Demo', status: 'published'}]});
    if (u.includes('/api/skills/demo-skill/markdown')) return json({markdown: '# demo\\noriginal body'});
    return json({});
  };
}"""


@pytest.fixture
def page():
    if sync_playwright is None:
        pytest.fail('playwright is required for skills browser tests. Install requirements-dev.txt')
    with sync_playwright() as playwright:
        launch_kwargs = {'headless': True, 'args': ['--disable-dev-shm-usage', '--no-sandbox']}
        if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'):
            launch_kwargs['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH']
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={'width': 1000, 'height': 800})

        def serve(route):
            relative = route.request.url.split(ORIGIN + '/', 1)[1].split('?', 1)[0]
            if not relative:
                route.fulfill(body='<html><body><div class="admin-card"><div id="skills-list"></div></div></body></html>',
                              content_type='text/html')
            elif (ROOT / relative).is_file():
                route.fulfill(path=str(ROOT / relative))
            else:
                route.fulfill(status=404, body='Not found')

        page.route(ORIGIN + '/**', serve)
        page.goto(ORIGIN + '/')
        page.evaluate(FETCH_STUB)
        page.evaluate("async () => { const m = await import('/static/js/skills.js'); await m.loadSkills(); }")
        page.wait_for_selector('.skill-kebab-btn')
        yield page
        browser.close()


def _menu_edit(page):
    page.click('.skill-kebab-btn')
    page.locator('.skill-kebab-menu .skill-kebab-item', has_text='Edit').first.click()
    page.wait_for_selector('.skill-md-editor')


def test_menu_edit_while_editing_keeps_editor_and_does_not_save(page):
    _menu_edit(page)
    page.fill('.skill-md-editor', '# demo\nhalf-finished edit')
    # Escape collapses the expanded card but keeps the editor in the DOM; the
    # collapsed header shows the kebab again (the user-reachable path).
    page.keyboard.press('Escape')
    page.wait_for_selector('.skill-card:not(.doclib-card-expanded)')
    _menu_edit(page)
    page.wait_for_timeout(200)
    assert page.evaluate('window.__posts') == []
    assert page.input_value('.skill-md-editor') == '# demo\nhalf-finished edit'
    assert page.evaluate("document.activeElement?.classList.contains('skill-md-editor')")


def test_footer_save_still_saves(page):
    _menu_edit(page)
    page.fill('.skill-md-editor', '# demo\nfinished edit')
    page.locator('.skill-card-preview .doclib-card-action-btn', has_text='Save').first.click()
    page.wait_for_function('window.__posts.length > 0')
    assert page.evaluate('window.__posts') == ['POST /api/skills/demo-skill/markdown']

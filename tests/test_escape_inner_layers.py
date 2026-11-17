"""Browser regression coverage for Escape inside document and email windows."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags


ROOT = Path(__file__).resolve().parents[1]


def test_rich_escape_closes_toolbar_then_selection_badge() -> None:
    script = r'''
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=escape-regression-1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'escape-doc', title: 'Escape', language: 'richtext',
          current_content: '<p>Selected sentence for testing</p>', version_count: 1,
        });
      });
      await page.waitForSelector('#doc-email-richbody p');
      await page.evaluate(() => {
        const rich = document.querySelector('#doc-email-richbody');
        const range = document.createRange();
        range.selectNodeContents(rich.querySelector('p'));
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        rich.focus();
        rich.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: 20, clientY: 20 }));
      });
      await page.waitForSelector('#doc-rich-selection-toolbar');
      await page.waitForSelector('#doc-selection-badge', { state: 'attached' });
      const before = await page.evaluate(() => ({
        toolbar: !!document.querySelector('#doc-rich-selection-toolbar'),
        badge: document.querySelector('#doc-selection-badge')?.style.display ?? 'missing',
      }));
      await page.keyboard.press('Escape');
      await page.waitForTimeout(40);
      const afterOne = await page.evaluate(() => ({
        toolbar: !!document.querySelector('#doc-rich-selection-toolbar'),
        badge: document.querySelector('#doc-selection-badge')?.style.display ?? 'missing',
      }));
      await page.keyboard.press('Escape');
      await page.waitForTimeout(40);
      const afterTwo = await page.evaluate(() => ({
        toolbar: !!document.querySelector('#doc-rich-selection-toolbar'),
        badge: document.querySelector('#doc-selection-badge')?.style.display ?? 'missing',
      }));
      console.log(JSON.stringify({ before, afterOne, afterTwo }));
      await browser.close();
    '''
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['before']['toolbar'] is True
    assert data['before']['badge'] != 'missing'
    assert data['afterOne'] == {'toolbar': False, 'badge': 'none'}
    assert data['afterTwo'] == {'toolbar': False, 'badge': 'none'}


def test_email_escape_closes_inner_states_without_closing_library() -> None:
    script = r'''
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage();
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        window.fetch = async () => new Response(JSON.stringify({
          accounts: [], emails: [], total: 0, folders: [], config: {},
        }), { status: 200, headers: { 'content-type': 'application/json' } });
        const mod = await import('/static/js/emailLibrary.js?v=escape-regression-2');
        const state = await import('/static/js/emailLibrary/state.js');
        mod.openEmailLibrary();
        await new Promise(resolve => setTimeout(resolve, 80));
        const modal = document.querySelector('#email-lib-modal');
        modal.classList.add('email-settings-mode');
        state.state._escapeRegression = { modal };
      });
      await page.keyboard.press('Escape');
      const settings = await page.evaluate(() => ({
        modal: !!document.querySelector('#email-lib-modal'),
        settings: document.querySelector('#email-lib-modal')?.classList.contains('email-settings-mode'),
      }));
      await page.evaluate(async () => {
        const state = (await import('/static/js/emailLibrary/state.js')).state;
        const modal = document.querySelector('#email-lib-modal');
        state._selectMode = true;
      });
      await page.keyboard.press('Escape');
      const select = await page.evaluate(async () => ({
        modal: !!document.querySelector('#email-lib-modal'),
        select: (await import('/static/js/emailLibrary/state.js')).state._selectMode,
        reading: document.querySelector('#email-lib-modal')?.classList.contains('email-reading'),
      }));
      await page.evaluate(() => document.querySelector('#email-lib-modal').classList.add('email-reading'));
      await page.keyboard.press('Escape');
      const reading = await page.evaluate(() => ({
        modal: !!document.querySelector('#email-lib-modal'),
        reading: document.querySelector('#email-lib-modal')?.classList.contains('email-reading'),
      }));
      console.log(JSON.stringify({ settings, select, reading }));
      await browser.close();
    '''
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['settings'] == {'modal': True, 'settings': False}
    assert data['select'] == {'modal': True, 'select': False, 'reading': False}
    assert data['reading'] == {'modal': True, 'reading': False}

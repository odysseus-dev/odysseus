"""Interaction coverage for rich-text formatting dropdown menus."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_rich_toolbar_menus_expose_keyboard_and_context_state():
    dropdown = DOC_JS.split("function _showMdDropdown", 1)[1].split(
        "function initMdToolbar", 1
    )[0]
    toolbar = DOC_JS.split("function initMdToolbar", 1)[1].split(
        "function _applyDocFont", 1
    )[0]

    assert "menu.setAttribute('role', 'menu')" in dropdown
    assert ": 'menuitem')" in dropdown
    assert "toggleBtn.setAttribute('aria-expanded', 'true')" in dropdown
    assert "needsTableSelection" in dropdown
    assert "needsImageSelection" in dropdown
    assert "ev.key === 'ArrowDown'" in dropdown
    assert "ev.key === 'ArrowUp'" in dropdown
    assert "ev.key === 'Home'" in dropdown
    assert "ev.key === 'End'" in dropdown
    assert "dismiss(true)" in dropdown
    assert "window.visualViewport?.width" in dropdown
    assert "window.visualViewport?.height" in dropdown
    assert "menu.style.zIndex = String(topPortalZ())" in dropdown
    assert "_showMdDropdown(dd, e.key === 'ArrowUp' ? -1 : 0, ++_mdDdActivationSerial)" in toolbar
    assert ".doc-overflow-item:focus-visible" in STYLE
    assert ".doc-md-toolbar button:focus-visible" in STYLE
    assert "function _richDropdownCurrentActions" in DOC_JS
    assert "it.setAttribute('aria-checked'" in dropdown
    assert "it.classList.add('is-current')" in dropdown
    assert "menuitemcheckbox" in dropdown
    assert "menuitemradio" in dropdown
    assert ".doc-overflow-item.is-current" in STYLE
    assert ".md-dd-current" in STYLE


def test_mobile_toolbar_uses_native_momentum_and_distinct_activation_tokens():
    toolbar = DOC_JS.split("function initMdToolbar", 1)[1].split(
        "function _applyDocFont", 1
    )[0]
    assert "toolbar.addEventListener('pointerdown'" in toolbar
    assert "_mdDdActivationSerial" in toolbar
    assert "itemsWrap.addEventListener('touchend'" not in toolbar
    assert "-webkit-overflow-scrolling: touch" in STYLE
    assert "touch-action: pan-x pan-y" in STYLE

    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&toggle-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'toggle-doc', title: 'Toggle menu', language: 'richtext',
          current_content: '<p>Toggle target</p>', version_count: 1,
        });
      });
      await page.waitForSelector('#doc-email-richbody p');

      const toggle = page.locator('button[data-dd="font"]');
      await toggle.click();
      const opened = await page.locator('#doc-md-dd-menu[data-dd="font"]').count();
      await toggle.click();
      const closed = await page.locator('#doc-md-dd-menu').count();

      const sizeToggle = page.locator('button[data-dd="textsize"]');
      await sizeToggle.click();
      const sliderOpened = await page.locator('.rich-toolbar-range').count();
      await sizeToggle.click();
      const sliderClosed = await page.locator('#doc-md-dd-menu').count();

      const colorToggle = page.locator('button[data-dd="color"]');
      await colorToggle.click();
      await page.locator('.rich-toolbar-color-input').click();
      const pickerOpened = await page.locator('.cp-popover').count();
      await colorToggle.click();
      const colorClosed = await page.locator('#doc-md-dd-menu, .cp-popover').count();

      const items = page.locator('#md-toolbar-items');
      const before = await items.evaluate(el => el.scrollLeft);
      await items.hover({ position: { x: 180, y: 12 } });
      await page.mouse.wheel(280, 0);
      await page.waitForTimeout(80);
      const scroll = await items.evaluate(el => ({
        left: el.scrollLeft,
        max: el.scrollWidth - el.clientWidth,
        touchAction: getComputedStyle(el).touchAction,
      }));
      console.log(JSON.stringify({ opened, closed, sliderOpened, sliderClosed, pickerOpened, colorClosed, before, scroll }));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["opened"] == 1
    assert data["closed"] == 0
    assert data["sliderOpened"] == 1
    assert data["sliderClosed"] == 0
    assert data["pickerOpened"] == 1
    assert data["colorClosed"] == 0
    assert data["scroll"]["max"] > 0
    assert data["scroll"]["left"] > data["before"]
    assert data["scroll"]["touchAction"] == "pan-x pan-y"


def test_mobile_toolbar_menu_preserves_selection_and_restores_focus():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&toolbar-menu-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'toolbar-menu-doc',
          title: 'Toolbar menu',
          language: 'richtext',
          current_content: '<p>Paragraph</p>',
          version_count: 1,
        });
      });
      await page.waitForSelector('#doc-email-richbody p');
      await page.evaluate(() => {
        const paragraph = document.querySelector('#doc-email-richbody p');
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.querySelector('[data-dd="heading"]').focus();
      });

      const imageInitiallyDisabled = await page.locator('[data-dd="image"]').isDisabled();
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(50);
      const opened = await page.evaluate(() => {
        const menu = document.querySelector('#doc-md-dd-menu');
        const rect = menu.getBoundingClientRect();
        return {
          role: menu.getAttribute('role'),
          labelledBy: menu.getAttribute('aria-labelledby'),
          expanded: document.querySelector('[data-dd="heading"]').getAttribute('aria-expanded'),
          focused: document.activeElement.textContent.trim(),
          topLayerRole: document.elementFromPoint(rect.left + 20, rect.top + 20)?.closest?.('[role]')?.getAttribute('role'),
          rect: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom },
        };
      });
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(50);
      const heading = await page.locator('#doc-email-richbody').innerHTML();

      await page.locator('[data-dd="table"]').focus();
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(50);
      const tableMenu = await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('#doc-md-dd-menu .doc-overflow-item'));
        const rect = document.querySelector('#doc-md-dd-menu').getBoundingClientRect();
        return {
          enabled: items.filter(item => !item.disabled).map(item => item.textContent.trim()),
          disabled: items.filter(item => item.disabled).map(item => item.textContent.trim()),
          focused: document.activeElement.textContent.trim(),
          rect: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom },
        };
      });
      await page.keyboard.press('End');
      const endFocus = await page.evaluate(() => document.activeElement.textContent.trim());
      await page.keyboard.press('Home');
      const homeFocus = await page.evaluate(() => document.activeElement.textContent.trim());
      await page.keyboard.press('Escape');
      const escaped = await page.evaluate(() => ({
        menu: Boolean(document.querySelector('#doc-md-dd-menu')),
        focusedToggle: document.activeElement?.dataset?.dd,
        expanded: document.querySelector('[data-dd="table"]').getAttribute('aria-expanded'),
      }));
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({
        imageInitiallyDisabled, opened, heading, tableMenu, endFocus, homeFocus, escaped, overflow,
      }));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["imageInitiallyDisabled"] is True
    assert data["opened"]["role"] == "menu"
    assert data["opened"]["labelledBy"] == "doc-md-dd-toggle-heading"
    assert data["opened"]["expanded"] == "true"
    assert data["opened"]["focused"] == "PParagraph"
    assert data["opened"]["topLayerRole"] == "menuitemradio"
    assert data["opened"]["rect"]["left"] >= 8
    assert data["opened"]["rect"]["right"] <= 382
    assert data["opened"]["rect"]["top"] >= 8
    assert data["opened"]["rect"]["bottom"] <= 836
    assert data["heading"] == "<h1>Paragraph</h1>"
    assert len(data["tableMenu"]["enabled"]) == 3
    assert len(data["tableMenu"]["disabled"]) == 14
    assert "↔Merge with right" in data["tableMenu"]["disabled"]
    assert "÷Split cell" in data["tableMenu"]["disabled"]
    assert "↕Align cell middle" in data["tableMenu"]["disabled"]
    assert data["tableMenu"]["focused"].startswith("2×2")
    assert data["endFocus"].startswith("4×4")
    assert data["homeFocus"].startswith("2×2")
    assert data["escaped"] == {"menu": False, "focusedToggle": "table", "expanded": "false"}
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]


def test_rich_toolbar_menus_track_live_formatting_values():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&toolbar-state-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'toolbar-state-doc',
          title: 'Toolbar states',
          language: 'richtext',
          current_content: '<p>Stateful text</p>',
          version_count: 1,
        });
      });
      await page.waitForSelector('#doc-email-richbody p');
      await page.evaluate(() => {
        const paragraph = document.querySelector('#doc-email-richbody p');
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        paragraph.closest('[contenteditable]').focus();
      });

      async function openMenu(kind) {
        await page.locator(`[data-dd="${kind}"]`).focus();
        await page.keyboard.press('ArrowDown');
        await page.waitForTimeout(30);
      }
      async function currentItems() {
        return page.evaluate(() => Array.from(document.querySelectorAll('#doc-md-dd-menu .is-current')).map(item => ({
          label: item.textContent.trim(),
          role: item.getAttribute('role'),
          checked: item.getAttribute('aria-checked'),
        })));
      }

      await openMenu('heading');
      const initialHeading = await currentItems();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Heading 2' }).click();
      await openMenu('heading');
      const heading = await currentItems();
      await page.keyboard.press('Escape');

      await openMenu('align');
      const initialAlign = await currentItems();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Align center' }).click();
      await openMenu('align');
      const align = await currentItems();
      await page.keyboard.press('Escape');

      await openMenu('spacing');
      const initialSpacing = await currentItems();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'One and a half' }).click();
      await openMenu('spacing');
      const spacing = await currentItems();
      await page.keyboard.press('Escape');

      await page.evaluate(() => {
        const image = document.createElement('img');
        image.className = 'richtext-image richtext-image-size-60 richtext-image-align-center';
        image.alt = 'Example';
        image.width = 120;
        image.height = 60;
        document.querySelector('#doc-email-richbody').appendChild(image);
      });
      await page.locator('#doc-email-richbody img').click();
      const imageButtonEnabled = !(await page.locator('[data-dd="image"]').isDisabled());
      await openMenu('image');
      const image = await currentItems();
      const imageActionRole = await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Edit description' }).getAttribute('role');
      await page.keyboard.press('Escape');

      const html = await page.locator('#doc-email-richbody').innerHTML();
      console.log(JSON.stringify({
        initialHeading, heading, initialAlign, align, initialSpacing, spacing,
        imageButtonEnabled, image, imageActionRole, html,
      }));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["initialHeading"] == [{"label": "PParagraph", "role": "menuitemradio", "checked": "true"}]
    assert data["heading"] == [{"label": "H2Heading 2", "role": "menuitemradio", "checked": "true"}]
    assert data["initialAlign"] == [{"label": "≡Align left", "role": "menuitemradio", "checked": "true"}]
    assert data["align"] == [{"label": "≡Align center", "role": "menuitemradio", "checked": "true"}]
    assert data["initialSpacing"] == [{"label": "AutoNormal", "role": "menuitemradio", "checked": "true"}]
    assert data["spacing"] == [{"label": "1.5One and a half", "role": "menuitemradio", "checked": "true"}]
    assert data["imageButtonEnabled"] is True
    assert data["image"] == [
        {"label": "60%Medium", "role": "menuitemcheckbox", "checked": "true"},
        {"label": "↔Align center", "role": "menuitemcheckbox", "checked": "true"},
    ]
    assert data["imageActionRole"] == "menuitem"
    assert 'style="text-align: center; line-height: 1.5;"' in data["html"]

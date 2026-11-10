"""Browser coverage for Rich Text slash-command block insertion."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_slash_menu_reuses_rich_text_actions_and_is_accessible():
    for action in (
        "paragraph",
        "h1",
        "h2",
        "h3",
        "ul",
        "ol",
        "check",
        "quote",
        "codeblock",
        "hr",
        "table:insert:3:3",
        "image",
    ):
        assert f"action: '{action}'" in DOC_JS
    assert "applyMdFormat(command.action)" in DOC_JS
    assert "document.execCommand('delete')" in DOC_JS
    assert "menu.setAttribute('role', 'listbox')" in DOC_JS
    assert "button.setAttribute('role', 'option')" in DOC_JS
    assert "rich.setAttribute('aria-activedescendant', activeItem.id)" in DOC_JS
    assert "itemBottom - list.clientHeight" in DOC_JS
    assert ".doc-rich-slash-menu" in STYLE


def test_slash_menu_filters_converts_blocks_inserts_tables_and_fits_mobile():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&slash-menu-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'slash-menu-doc',
          title: 'Slash menu',
          language: 'richtext',
          current_content: '<p>Opening paragraph</p><p><br></p>',
          version_count: 1,
        });
      });
      await page.waitForFunction(() => document.querySelectorAll('#doc-email-richbody p').length >= 2);
      await page.evaluate(() => {
        const block = document.querySelector('#doc-email-richbody p:last-child');
        const range = document.createRange();
        range.selectNodeContents(block);
        range.collapse(true);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        block.closest('[contenteditable]').focus();
      });

      await page.keyboard.type('/head');
      await page.waitForSelector('#doc-rich-slash-menu');
      const filtered = await page.locator('.doc-rich-slash-label').allTextContents();
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Section title');
      const heading = await page.locator('#doc-email-richbody h2').textContent();
      const noHeadingQuery = !(await page.locator('#doc-email-richbody').textContent()).includes('/head');

      await page.evaluate(() => {
        const rich = document.querySelector('#doc-email-richbody');
        const paragraph = document.createElement('p');
        paragraph.innerHTML = '<br>';
        rich.appendChild(paragraph);
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(true);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        rich.focus();
      });
      await page.keyboard.type('/tab');
      await page.waitForSelector('#doc-rich-slash-menu');
      const tableFiltered = await page.locator('.doc-rich-slash-label').allTextContents();
      await page.locator('.doc-rich-slash-item').click();
      const table = await page.locator('#doc-email-richbody table').evaluate(el => ({
        rows: el.rows.length,
        cells: Array.from(el.rows).map(row => row.cells.length),
      }));
      const noTableQuery = !(await page.locator('#doc-email-richbody').textContent()).includes('/tab');

      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => {
        const rich = document.querySelector('#doc-email-richbody');
        const paragraph = document.createElement('p');
        paragraph.innerHTML = '<br>';
        rich.appendChild(paragraph);
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(true);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        rich.focus();
      });
      await page.keyboard.type('/');
      await page.waitForSelector('#doc-rich-slash-menu');
      await page.mouse.move(1, 1);
      await page.keyboard.press('End');
      const mobile = await page.locator('#doc-rich-slash-menu').evaluate(el => {
        const rect = el.getBoundingClientRect();
        const list = el.querySelector('.doc-rich-slash-list');
        const active = el.querySelector('.doc-rich-slash-item.is-active');
        const rich = document.querySelector('#doc-email-richbody');
        return {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          viewport: [innerWidth, innerHeight],
          active: el.querySelectorAll('.doc-rich-slash-item.is-active').length,
          activeLabel: active?.querySelector('.doc-rich-slash-label')?.textContent,
          activeDescendant: rich?.getAttribute('aria-activedescendant'),
          activeId: active?.id,
          scrollTop: list?.scrollTop || 0,
        };
      });
      await page.keyboard.press('Home');
      const homeOptions = await page.locator('.doc-rich-slash-label').allTextContents();
      const homeLabel = await page.locator('.doc-rich-slash-item.is-active .doc-rich-slash-label').textContent();
      await page.keyboard.press('Escape');
      const escaped = await page.locator('#doc-rich-slash-menu').count() === 0;
      const slashRemains = (await page.locator('#doc-email-richbody').textContent()).trim().endsWith('/');
      const cleanedAria = await page.locator('#doc-email-richbody').evaluate(el => ({
        controls: el.hasAttribute('aria-controls'),
        expanded: el.hasAttribute('aria-expanded'),
        activeDescendant: el.hasAttribute('aria-activedescendant'),
        popup: el.hasAttribute('aria-haspopup'),
      }));

      console.log(JSON.stringify({ filtered, heading, noHeadingQuery, tableFiltered, table, noTableQuery, mobile, homeOptions, homeLabel, escaped, slashRemains, cleanedAria }));
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
    assert data["filtered"] == [
        "Heading 1", "Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6",
    ]
    assert data["heading"] == "Section title"
    assert data["noHeadingQuery"] is True
    assert data["tableFiltered"] == ["Table"]
    assert data["table"] == {"rows": 3, "cells": [3, 3, 3]}
    assert data["noTableQuery"] is True
    mobile = data["mobile"]
    assert mobile["left"] >= 0 and mobile["right"] <= mobile["viewport"][0]
    assert mobile["top"] >= 0 and mobile["bottom"] <= mobile["viewport"][1]
    assert mobile["active"] == 1
    assert mobile["activeLabel"] == "Image"
    assert mobile["activeDescendant"] == mobile["activeId"]
    assert mobile["scrollTop"] > 0
    assert data["homeOptions"][0] == "Text"
    assert data["homeLabel"] == data["homeOptions"][0], data
    assert data["escaped"] is True
    assert data["slashRemains"] is True
    assert data["cleanedAria"] == {
        "controls": False,
        "expanded": False,
        "activeDescendant": False,
        "popup": False,
    }

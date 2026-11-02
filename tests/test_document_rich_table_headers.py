"""Browser coverage for undoable rich-text table header controls."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_table_header_controls_use_tag_replacement_and_native_history():
    table_actions = DOC_JS.split("function _applyRichTableAction", 1)[1].split(
        "function applyMdFormat", 1
    )[0]
    menu_state = function_body("_richDropdownCurrentActions")

    assert "function _replaceRichTableCellTag" in DOC_JS
    assert "table:toggle-header-row" in table_actions
    assert "table:toggle-header-column" in table_actions
    assert "const headerModes = _richTableHeaderModes(clone)" in table_actions
    assert "headerModes.headerRow = !headerModes.headerRow" in table_actions
    assert "headerModes.headerColumn = !headerModes.headerColumn" in table_actions
    assert "_applyRichTableHeaderModes(clone, headerModes)" in table_actions
    header_normalizer = function_body("_applyRichTableHeaderModes")
    assert "_replaceRichTableCellTag" in header_normalizer
    assert "_replaceRichTable(rich, original, clone" in table_actions
    assert "current.add('table:toggle-header-row')" in menu_state
    assert "current.add('table:toggle-header-column')" in menu_state
    assert "tableHeaderToggle" in DOC_JS


def test_mobile_header_row_and_column_toggle_independently_with_undo():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&table-header-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'table-header-doc',
          title: 'Table headers',
          language: 'richtext',
          current_content: '<table><tbody><tr><th>Name</th><th>Value</th></tr><tr><td>One</td><td>1</td></tr></tbody></table><p>After</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
        const cell = document.querySelector('#doc-email-richbody tr:last-child td:first-child');
        const range = document.createRange();
        range.selectNodeContents(cell);
        range.collapse(true);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        cell.closest('[contenteditable]').focus();
      });

      async function openTableMenu() {
        await page.locator('[data-dd="table"]').focus();
        await page.keyboard.press('ArrowDown');
        await page.waitForTimeout(30);
      }
      async function state() {
        return page.evaluate(() => ({
          tags: Array.from(document.querySelectorAll('#doc-email-richbody tr')).map(row =>
            Array.from(row.cells).map(cell => cell.tagName)
          ),
          checks: Object.fromEntries(Array.from(document.querySelectorAll('#doc-md-dd-menu [aria-checked]')).map(item => [
            item.textContent.trim(), item.getAttribute('aria-checked'),
          ])),
          roles: Array.from(document.querySelectorAll('#doc-md-dd-menu [aria-checked]')).map(item => item.getAttribute('role')),
        }));
      }

      await openTableMenu();
      const initial = await state();
      const menuRect = await page.locator('#doc-md-dd-menu').evaluate(menu => {
        const rect = menu.getBoundingClientRect();
        return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      });
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Header column' }).click();
      await page.waitForTimeout(50);
      const columnOn = await page.evaluate(() => ({
        tags: Array.from(document.querySelectorAll('#doc-email-richbody tr')).map(row => Array.from(row.cells).map(cell => cell.tagName)),
        undoDisabled: document.querySelector('#doc-undo-btn').disabled,
      }));
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(50);
      const undone = await page.evaluate(() => Array.from(document.querySelectorAll('#doc-email-richbody tr')).map(row =>
        Array.from(row.cells).map(cell => cell.tagName)
      ));
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(50);
      const redone = await page.evaluate(() => Array.from(document.querySelectorAll('#doc-email-richbody tr')).map(row =>
        Array.from(row.cells).map(cell => cell.tagName)
      ));

      await openTableMenu();
      const bothOn = await state();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Header row' }).click();
      await page.waitForTimeout(50);
      await openTableMenu();
      const rowOff = await state();
      await page.keyboard.press('Escape');
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({ initial, menuRect, columnOn, undone, redone, bothOn, rowOff, overflow }));
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
    header_checks = lambda state: {
        label: value for label, value in state["checks"].items() if "Header" in label
    }
    assert data["initial"]["tags"] == [["TH", "TH"], ["TD", "TD"]]
    assert header_checks(data["initial"]) == {"H↔Header row": "true", "H↕Header column": "false"}
    assert data["initial"]["roles"].count("menuitemcheckbox") == 2
    assert data["initial"]["roles"].count("menuitemradio") == 3
    assert data["menuRect"]["left"] >= 8
    assert data["menuRect"]["right"] <= 382
    assert data["menuRect"]["top"] >= 8
    assert data["menuRect"]["bottom"] <= 836
    assert data["columnOn"]["tags"] == [["TH", "TH"], ["TH", "TD"]]
    assert data["columnOn"]["undoDisabled"] is False
    assert data["undone"] == [["TH", "TH"], ["TD", "TD"]]
    assert data["redone"] == [["TH", "TH"], ["TH", "TD"]]
    assert header_checks(data["bothOn"]) == {"H↔Header row": "true", "H↕Header column": "true"}
    assert data["rowOff"]["tags"] == [["TH", "TD"], ["TH", "TD"]]
    assert header_checks(data["rowOff"]) == {"H↔Header row": "false", "H↕Header column": "true"}
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]

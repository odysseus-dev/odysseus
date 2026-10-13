"""Browser coverage for undoable and protocol-safe Rich Text URL paste."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags


ROOT = Path(__file__).resolve().parents[1]


def test_rich_url_paste_links_selections_and_plain_urls_without_unsafe_autolinks():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&smart-link-paste=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'smart-link-paste-doc',
          title: 'Smart link paste',
          language: 'richtext',
          current_content: '<p><strong>Select</strong> this text</p><p>Paste here: </p><p>Unsafe target</p><p>Ordinary target</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 400));
      });

      async function selectBlock(index, collapse = false) {
        await page.evaluate(({ index, collapse }) => {
          const rich = document.querySelector('#doc-email-richbody');
          const block = rich.children[index];
          const range = document.createRange();
          range.selectNodeContents(block);
          if (collapse) range.collapse(false);
          const selection = getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          rich.focus();
        }, { index, collapse });
      }

      async function paste(text, html = '') {
        await page.evaluate(({ text, html }) => {
          const data = new DataTransfer();
          data.setData('text/plain', text);
          if (html) data.setData('text/html', html);
          document.querySelector('#doc-email-richbody').dispatchEvent(new ClipboardEvent('paste', {
            clipboardData: data,
            bubbles: true,
            cancelable: true,
          }));
        }, { text, html });
        await page.waitForTimeout(80);
      }

      await selectBlock(0);
      await paste('example.com/reference');
      const selectedLink = await page.locator('#doc-email-richbody a').first().evaluate(link => ({
        href: link.getAttribute('href'),
        text: link.textContent,
        bold: link.querySelector('strong')?.textContent || '',
        rel: link.getAttribute('rel'),
      }));
      const storedAfterPaste = await page.locator('#doc-editor-textarea').inputValue();

      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(80);
      const afterUndo = await page.locator('#doc-email-richbody').evaluate(root => ({
        links: root.querySelectorAll('a').length,
        text: root.children[0].textContent,
        bold: root.children[0].querySelector('strong')?.textContent || '',
      }));
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(80);
      const afterRedoLinks = await page.locator('#doc-email-richbody a').count();

      await selectBlock(1, true);
      await paste('https://openai.com/docs');
      const caretLink = await page.locator('#doc-email-richbody a').nth(1).evaluate(link => ({
        href: link.getAttribute('href'),
        text: link.textContent,
      }));

      await selectBlock(2);
      await paste('javascript:alert(1)');
      await selectBlock(3);
      await paste('not a URL');
      const plainResults = await page.locator('#doc-email-richbody').evaluate(root => ({
        unsafeText: root.children[2].textContent,
        ordinaryText: root.children[3].textContent,
        links: root.querySelectorAll('a').length,
        stored: document.querySelector('#doc-editor-textarea').value,
      }));

      await page.locator('#doc-email-richbody').evaluate(root => {
        const paragraph = document.createElement('p');
        paragraph.textContent = 'Email: ';
        root.appendChild(paragraph);
      });
      await selectBlock(4, true);
      await paste('person@example.com');
      const emailLink = await page.locator('#doc-email-richbody a').nth(2).evaluate(link => ({
        href: link.getAttribute('href'),
        text: link.textContent,
      }));

      const crossBlock = await page.evaluate(() => {
        const rich = document.querySelector('#doc-email-richbody');
        const first = document.createElement('p');
        const second = document.createElement('p');
        first.textContent = 'Cross one';
        second.textContent = 'Cross two';
        rich.append(first, second);
        const range = document.createRange();
        range.setStart(first.firstChild, 0);
        range.setEnd(second.firstChild, second.firstChild.length);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        rich.focus();
        const data = new DataTransfer();
        data.setData('text/plain', 'cross.example.com');
        rich.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
        return {
          links: rich.querySelectorAll('a').length,
          text: rich.textContent,
        };
      });

      console.log(JSON.stringify({ selectedLink, storedAfterPaste, afterUndo, afterRedoLinks, caretLink, plainResults, emailLink, crossBlock }));
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

    assert data["selectedLink"] == {
        "href": "https://example.com/reference",
        "text": "Select this text",
        "bold": "Select",
        "rel": "noopener noreferrer",
    }
    assert '<a href="https://example.com/reference"' in data["storedAfterPaste"]
    assert data["afterUndo"] == {"links": 0, "text": "Select this text", "bold": "Select"}
    assert data["afterRedoLinks"] == 1
    assert data["caretLink"] == {
        "href": "https://openai.com/docs",
        "text": "https://openai.com/docs",
    }
    assert data["plainResults"]["unsafeText"] == "javascript:alert(1)"
    assert data["plainResults"]["ordinaryText"] == "not a URL"
    assert data["plainResults"]["links"] == 2
    assert "javascript:alert(1)" in data["plainResults"]["stored"]
    assert "not a URL" in data["plainResults"]["stored"]
    assert data["emailLink"] == {
        "href": "mailto:person@example.com",
        "text": "person@example.com",
    }
    assert data["crossBlock"]["links"] == 3
    assert "cross.example.com" in data["crossBlock"]["text"]

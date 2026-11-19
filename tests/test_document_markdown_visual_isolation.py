"""Markdown Visual mode must not change how HTML (rich text) documents edit.

The Visual editor shares its contenteditable surface and wiring with the rich text
and email editors. Everything specific to Markdown is gated on Visual mode; these
checks pin the behavior of the other editors and the Markdown-only guards.
"""

from tests.test_document_visual_editor import _run_browser

RICH_DOC = "{ id: 'rich-doc', title: 'Rich', language: 'richtext', current_content: '<p>Hello world</p>' }"
MD_DOC = "{ id: 'md-doc', title: 'Markdown', language: 'markdown', current_content: 'Hello world' }"


def test_rich_text_editor_keeps_its_input_selection_toolbar_and_menu_behavior():
    data = _run_browser(r"""
      await boot('isolation-rich');
      await open(__RICH__);
      await page.waitForSelector('#doc-email-richbody.richtext-mode', { state: 'visible' });
      const rich = page.locator('#doc-email-richbody');

      // IME composition: rich text keeps synchronizing on every input event.
      const composition = await page.evaluate(() => {
        const el = document.getElementById('doc-email-richbody');
        el.focus();
        el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
        el.firstElementChild.textContent = 'Hello world composing';
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertCompositionText', isComposing: true }));
        return document.getElementById('doc-editor-textarea').value;
      });

      // Legacy figure captions anywhere in the document are normalized on input, not
      // only inside the block under the caret.
      const image = await page.evaluate(() => {
        const el = document.getElementById('doc-email-richbody');
        el.insertAdjacentHTML('beforeend', '<figure class="richtext-image"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="x"><figcaption>cap</figcaption></figure>');
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
        return { captions: el.querySelectorAll('div.richtext-image-caption').length, legacy: el.querySelectorAll('figcaption').length };
      });

      // Selecting text raises the historical floating toolbar.
      await page.evaluate(() => {
        const el = document.getElementById('doc-email-richbody');
        const range = document.createRange();
        range.selectNodeContents(el.firstElementChild);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        el.focus();
        document.dispatchEvent(new Event('selectionchange'));
      });
      await page.waitForSelector('#doc-rich-selection-toolbar', { state: 'visible' });
      const toolbarActions = await page.evaluate(() => [...document.querySelectorAll('#doc-rich-selection-toolbar [data-rich-selection-action]')]
        .map(b => b.dataset.richSelectionAction));

      // Ctrl+U still underlines rich text.
      await page.evaluate(() => {
        const el = document.getElementById('doc-email-richbody');
        const range = document.createRange(); range.selectNodeContents(el.firstElementChild);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); el.focus();
      });
      await page.keyboard.press('Control+u');
      const underlined = await page.evaluate(() => Boolean(document.querySelector('#doc-email-richbody u, #doc-email-richbody [style*="underline"]')));

      // Text typed straight into an empty editor gets no Markdown input rules.
      await open({ id: 'rich-empty', title: 'Empty', language: 'richtext', current_content: '' });
      await page.waitForFunction(() => window.__state.activeDocId === 'rich-empty');
      await rich.click();
      await page.keyboard.type('# Not a heading');
      const heading = await page.evaluate(() => document.querySelectorAll('#doc-email-richbody h1').length);

      // The code menu offers no Markdown code-block language items.
      await page.locator('[data-dd="code"]').click();
      const codeItems = await page.locator('#doc-md-dd-menu .doc-overflow-item').allTextContents();
      await finish({ composition, image, toolbarActions, underlined, heading, codeItems });
    """.replace("__RICH__", RICH_DOC))

    assert "composing" in data["composition"], "rich text synchronizes during composition"
    assert data["image"] == {"captions": 1, "legacy": 0}
    assert data["toolbarActions"] == [
        "bold", "italic", "underline", "strike", "link", "highlight:#fef08a", "insert-image", "removeformat",
    ]
    assert data["underlined"] is True
    assert data["heading"] == 0
    assert not [label for label in data["codeItems"] if "Language" in label], data["codeItems"]


def test_markdown_visual_mode_defers_composition_and_blocks_native_underline():
    data = _run_browser(r"""
      await boot('isolation-markdown');
      await open(__MD__);
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

      // Composition: the source is untouched until the composition ends.
      const composition = await page.evaluate(() => {
        const el = document.getElementById('doc-email-richbody');
        el.focus();
        el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
        el.firstElementChild.textContent = 'Hello world composing';
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertCompositionText', isComposing: true }));
        const during = window.__state.docs.get('md-doc').content;
        el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
        return { during, after: window.__state.docs.get('md-doc').content };
      });

      // Ctrl+U is consumed: no native underline, no page behavior.
      await page.evaluate(() => {
        const el = document.getElementById('doc-email-richbody');
        const range = document.createRange(); range.selectNodeContents(el.firstElementChild);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); el.focus();
        window.__prevented = [];
        document.addEventListener('keydown', (event) => { setTimeout(() => window.__prevented.push([event.key, event.defaultPrevented])); }, true);
      });
      await page.keyboard.press('Control+u');
      await page.keyboard.press('Meta+u');
      await page.waitForTimeout(100); // let the keydown recorders flush
      const underline = await page.evaluate(() => ({
        prevented: window.__prevented,
        html: document.getElementById('doc-email-richbody').innerHTML,
        content: window.__state.docs.get('md-doc').content,
      }));
      await finish({ composition, underline });
    """.replace("__MD__", MD_DOC))

    assert data["composition"]["during"] == "Hello world"
    assert data["composition"]["after"] == "Hello world composing"
    assert [prevented for key, prevented in data["underline"]["prevented"] if key == "u"] == [True, True]
    assert "<u" not in data["underline"]["html"] and "underline" not in data["underline"]["html"]
    assert data["underline"]["content"] == "Hello world composing"


def test_table_actions_keep_a_following_block_quote_separate():
    """Replacing a table must not merge it into the quote that directly follows it."""
    data = _run_browser(r"""
      await boot('table-before-quote');
      const out = {};
      for (const [id, language, body] of [
        ['tq-md', 'markdown', '| A | B |\n| --- | --- |\n| x | y |\n\n> [!NOTE]\n> After.\n\ntail'],
        ['tq-rt', 'richtext', '<table><tbody><tr><td>x</td><td>y</td></tr></tbody></table><blockquote><p>After.</p></blockquote><p>tail</p>'],
      ]) {
        await open({ id, title: id, language, current_content: body });
        await page.waitForFunction((docId) => window.__state.activeDocId === docId, id);
        await page.locator('#doc-email-richbody td').first().click();
        await page.locator('[data-dd="table"]').click();
        await page.locator('#doc-md-dd-menu .doc-overflow-item', { hasText: language === 'markdown' ? 'Column: align center' : 'Add row below' }).click();
        out[id] = await page.evaluate(() => ({
          insideQuote: document.querySelectorAll('#doc-email-richbody blockquote table').length,
          tables: document.querySelectorAll('#doc-email-richbody table').length,
          quotes: document.querySelectorAll('#doc-email-richbody blockquote').length,
        }));
      }
      await finish(out);
    """)
    for doc_id in ("tq-md", "tq-rt"):
        assert data[doc_id] == {"insideQuote": 0, "tables": 1, "quotes": 1}, data[doc_id]


def test_leaving_typed_inline_code_keeps_the_code_and_continues_in_plain_text():
    """Ctrl+` inside inline code used to select the code, so the next keystroke replaced it."""
    data = _run_browser(r"""
      await boot('inline-code-exit');
      const out = {};
      for (const [id, language, body] of [['ic-md', 'markdown', 'Start'], ['ic-rt', 'richtext', '<p>Start</p>']]) {
        await open({ id, title: id, language, current_content: body });
        await page.waitForFunction((docId) => window.__state.activeDocId === docId, id);
        await caretAtEnd();
        await page.keyboard.type(' run ');
        await page.keyboard.press('Control+`');
        await page.keyboard.type('npm test');
        await page.waitForTimeout(250);
        await page.keyboard.press('Control+`');
        await page.keyboard.type(' after');
        out[id] = await page.evaluate((docId) => ({
          text: document.getElementById('doc-email-richbody').textContent.replace(/​/g, ''),
          code: [...document.querySelectorAll('#doc-email-richbody code, #doc-email-richbody span[style*="OdysseusInlineCode"]')].map(el => el.textContent),
          content: window.__state.docs.get(docId)?.content,
        }), id);
      }
      await finish(out);
    """)
    assert data["ic-md"]["text"] == "Start run npm test after", data["ic-md"]
    assert data["ic-md"]["code"] == ["npm test"]
    assert data["ic-md"]["content"] == "Start run `npm test` after"
    assert data["ic-rt"]["text"] == "Start run npm test after", data["ic-rt"]
    assert data["ic-rt"]["code"] == ["npm test"]

"""Visual editing of loose, tight, nested and task lists keeps exact source bytes."""

import json

from tests.test_document_visual_editor import _run_browser

# (id, source, text node to extend, expected saved source)
EDITABLE_CASES = [
    ("loose-after-paragraph-edit-paragraph", "para\n\n- a\n\n- b", "para", "paraX\n\n- a\n\n- b"),
    ("loose-after-paragraph-edit-last", "para\n\n- a\n\n- b", "b", "para\n\n- a\n\n- bX"),
    ("loose-edit-middle", "- a\n\n- b\n\n- c", "b", "- a\n\n- bX\n\n- c"),
    ("loose-ordered", "1. one\n\n2. two\n\n3. three", "two", "1. one\n\n2. twoX\n\n3. three"),
    ("loose-task", "- [ ] a\n\n- [x] b", "b", "- [ ] a\n\n- [x] bX"),
    ("loose-star-marker", "* star\n\n* moon", "moon", "* star\n\n* moonX"),
    ("loose-nested", "- a\n  - a1\n\n- b", "a1", "- a\n  - a1X\n\n- b"),
    ("loose-repeated-identical", "- x\n\n- x\n\n- x", "x", "- x\n\n- x\n\n- xX"),
    ("loose-mixed-kinds", "- a\n\n1. b\n\n- c", "b", "- a\n\n1. bX\n\n- c"),
    ("loose-then-paragraph", "- a\n\n- b\n\ntail", "tail", "- a\n\n- b\n\ntailX"),
    ("loose-wide-separator", "- a\n\n\n\n- b", "a", "- aX\n\n\n\n- b"),
    ("tight-list", "para\n\n- a\n- b", "b", "para\n\n- a\n- bX"),
    ("tight-ordered", "1. a\n2. b", "b", "1. a\n2. bX"),
    ("tight-nested", "- a\n  - b\n    - c", "c", "- a\n  - b\n    - cX"),
    ("tight-task", "- [ ] a\n- [x] b", "a", "- [ ] aX\n- [x] b"),
    ("crlf-loose", "para\r\n\r\n- a\r\n\r\n- b", "b", "para\r\n\r\n- a\r\n\r\n- bX"),
]

# Constructs the visual list model cannot keep exactly are refused before any edit.
REFUSED_CASES = [
    ("multi-paragraph-item", "- a\n\n  more\n\n- b"),
    ("nested-after-blank", "- a\n\n  - b"),
]


def test_loose_and_tight_lists_edit_and_save_exact_source():
    cases = json.dumps(EDITABLE_CASES)
    data = _run_browser(r"""
      await boot('visual-lists');
      const cases = __CASES__;
      const results = [];
      for (const [id, source, target, expected] of cases) {
        await open({ id, title: id, language: 'markdown', current_content: source });
        await page.waitForFunction((docId) => window.__state.activeDocId === docId, id);
        const admitted = await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible', timeout: 3000 }).then(() => true, () => false);
        const reasons = await page.evaluate((docId) => window.__state.docs.get(docId)?._visualCompatReasons || [], id);
        let saved = null; let blocked = null;
        if (admitted) {
          await page.evaluate((needle) => {
            const rich = document.getElementById('doc-email-richbody');
            const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
            let node; let match = null;
            while ((node = walker.nextNode())) if (node.data === needle) match = node; // last occurrence
            if (!match) throw new Error('text node not found: ' + needle);
            match.data = needle + 'X';
            rich.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: 'X' }));
          }, target);
          blocked = await page.evaluate((docId) => Boolean(window.__state.docs.get(docId)?._visualSyncBlocked), id);
          await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
          saved = api.store.get(id)?.current_content;
        }
        results.push({ id, admitted, reasons, blocked, saved, expected });
      }
      await finish({ results });
    """.replace("__CASES__", cases))

    for result in data["results"]:
        assert result["admitted"], result
        assert result["blocked"] is False, result
        assert result["saved"] == result["expected"], result


def test_lists_the_visual_model_cannot_keep_exact_stay_in_source_mode():
    cases = json.dumps(REFUSED_CASES)
    data = _run_browser(r"""
      await boot('visual-lists-refused');
      const results = [];
      for (const [id, source] of __CASES__) {
        await open({ id, title: id, language: 'markdown', current_content: source });
        await page.waitForFunction((docId) => window.__state.activeDocId === docId, id);
        await page.waitForTimeout(300);
        results.push(await page.evaluate((docId) => ({
          id: docId,
          visual: document.getElementById('doc-email-richbody').classList.contains('markdown-visual-mode')
            && getComputedStyle(document.getElementById('doc-email-richbody')).display !== 'none',
          reasons: window.__state.docs.get(docId)?._visualCompatReasons || [],
          content: window.__state.docs.get(docId)?.content,
        }), id));
      }
      await finish({ results });
    """.replace("__CASES__", cases))

    by_id = {result["id"]: result for result in data["results"]}
    for case_id, source in REFUSED_CASES:
        assert by_id[case_id]["visual"] is False, by_id[case_id]
        assert by_id[case_id]["reasons"], by_id[case_id]
        assert by_id[case_id]["content"] == source


def test_adding_list_items_and_table_rows_keeps_crlf_documents_consistent():
    data = _run_browser(r"""
      await boot('visual-crlf');
      const append = async (needle, text) => {
        await page.evaluate((target) => {
          const rich = document.getElementById('doc-email-richbody');
          const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
          let node; let match = null;
          while ((node = walker.nextNode())) if (node.data === target) match = node;
          rich.focus();
          const range = document.createRange(); range.setStart(match, match.data.length); range.collapse(true);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        }, needle);
        await page.keyboard.press('Enter');
        await page.keyboard.type(text);
      };
      const results = {};
      const list = 'para\r\n\r\n- a\r\n- b\r\n\r\nend';
      await open({ id: 'crlf-list', title: 'crlf', language: 'markdown', current_content: list });
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode li');
      await append('b', 'c');
      await page.evaluate(() => window.__doc.saveDocument({ silent: true }));
      results.list = api.store.get('crlf-list')?.current_content;
      await finish(results);
    """)
    assert data["list"] == "para\r\n\r\n- a\r\n- b\r\n- c\r\n\r\nend", repr(data["list"])

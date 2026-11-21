"""A document Visual mode cannot edit exactly opens in Source mode and says why."""

import json

from tests.test_document_visual_editor import _run_browser

CASES = {
    "underscore-italic": "Intro\n\nSome _italic_ text",
    "titled-link": "Intro\n\n[docs](https://example.com \"Title\")",
    "inline-math": "Intro\n\nEnergy $x$ here",
    "footnote": "Intro[^1]\n\n[^1]: A note",
    "multi-paragraph-item": "- one\n\n  continued\n\n- two",
}


def test_unsupported_documents_open_in_source_with_a_specific_unalarming_reason():
    cases = json.dumps(CASES)
    data = _run_browser(r"""
      await boot('compat-banner');
      const results = {};
      for (const [id, source] of Object.entries(__CASES__)) {
        await open({ id, title: id, language: 'markdown', current_content: source });
        await page.waitForFunction((docId) => window.__state.activeDocId === docId, id);
        await page.waitForTimeout(250);
        results[id] = await page.evaluate((docId) => ({
          banner: document.getElementById('doc-visual-compat-banner').textContent,
          bannerVisible: getComputedStyle(document.getElementById('doc-visual-compat-banner')).display !== 'none',
          visual: getComputedStyle(document.getElementById('doc-email-richbody')).display !== 'none',
          sourceVisible: getComputedStyle(document.getElementById('doc-editor-wrap')).display !== 'none',
          content: window.__state.docs.get(docId).content,
        }), id);
      }
      await finish(results);
    """.replace("__CASES__", cases))

    for case_id, source in CASES.items():
        result = data[case_id]
        assert result["bannerVisible"] and not result["visual"] and result["sourceVisible"], result
        assert result["content"] == source, "opening never rewrites the document"
        banner = result["banner"]
        assert "Source mode" in banner and "has not been changed" in banner, banner
        assert "corrupt" not in banner.lower() and "error" not in banner.lower(), banner
    assert "line 3" in data["underscore-italic"]["banner"] or "line 2" in data["underscore-italic"]["banner"]
    assert "footnote" in data["footnote"]["banner"]
    assert "multi-paragraph list item" in data["multi-paragraph-item"]["banner"]

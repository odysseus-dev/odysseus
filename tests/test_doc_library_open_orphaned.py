"""Regression for issue #1602 — after closing an AI-written document, its "Open"
button in the Documents library is grayed out, so the user can't reopen it.

Root cause: closing/detaching a document nulls its session_id (the detach
behaviour from #1238), and both Open controls in static/js/documentLibrary.js
(the card's expanded Open button AND the card dropdown's Open item) gated on
`doc.session_id` — wiring `libraryOpenInSession` (which early-returns when there's
no session) and DISABLING the control otherwise. But the module already has
`libraryOpenDocument`, which explicitly handles the orphaned case ("just open in
editor without switching session"). The fix routes the no-session path there
instead of disabling.

documentLibrary.js pulls in browser-only modules so it can't run under node; this
guards the wiring at the source level (red→green via git-stash).
"""

import re
import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags

SRC = Path(__file__).resolve().parent.parent / "static/js/documentLibrary.js"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def test_orphaned_doc_open_controls_are_not_disabled():
    text = _src()
    # Neither Open control may hard-disable itself for a session-less doc anymore.
    assert "openItem.disabled = true" not in text, "dropdown Open must not be disabled for orphaned docs (#1602)"
    assert "openBtn.disabled = true" not in text, "card Open button must not be disabled for orphaned docs (#1602)"
    # The old 'not linked to a session' dead-end titles are gone.
    assert "not linked to a session" not in text.lower()


def test_orphaned_doc_open_routes_to_editor_load():
    """Both Open controls' no-session branch must call libraryOpenDocument, the
    function that opens an orphaned doc directly in the editor by id."""
    text = _src()
    # definition + two wirings (dropdown item + card button)
    assert text.count("libraryOpenDocument(doc)") >= 3, \
        "both Open controls must route the no-session case to libraryOpenDocument"
    # libraryOpenDocument genuinely handles the orphaned case.
    body = text[text.index("async function libraryOpenDocument(doc)"):]
    body = body[: body.index("async function libraryOpenInSession")]
    assert "if (!doc.session_id)" in body and "_loadDocument(doc.id)" in body, \
        "libraryOpenDocument must open a session-less doc by id"


def test_mobile_open_is_available_for_every_document():
    text = _src()
    mobile_menu = text.split("if (window.innerWidth <= 768)", 1)[1].split(
        "const dropdown =", 1
    )[0]

    assert "label: 'Open in new chat'" in mobile_menu
    assert "'Open in original' : 'Open document'" in mobile_menu
    assert "doc.session_id ? libraryOpenInSession(doc) : libraryOpenDocument(doc)" in mobile_menu
    assert "libraryImportDocument(doc, { newSession: true })" in mobile_menu


def test_session_linked_open_uses_explicit_document_loader():
    """The loader restores a minimized panel before selecting the document."""
    text = _src()
    body = text.split("async function libraryOpenInSession(doc)", 1)[1].split(
        "/** Copy a document", 1
    )[0]

    assert "await _loadDocument(doc.id);" in body
    assert "if (!_isOpenFn()) _openPanel();" not in body
    assert "_switchToDoc(doc.id);" not in body


def test_library_open_marks_full_editor_intent_before_session_switch():
    """The delayed session restore must not minimize an explicitly opened doc."""
    text = _src()
    for function_name in ("libraryOpenDocument", "libraryOpenInSession"):
        body = text.split(f"async function {function_name}(doc)", 1)[1]
        body = body.split("async function", 1)[0]
        assert "_prepareDocumentOpen?.(doc.session_id);" in body
        if "selectSession(doc.session_id)" in body:
            assert body.index("_prepareDocumentOpen?.(doc.session_id);") < body.index(
                "selectSession(doc.session_id)"
            )

    assert "setTimeout(r, 150)" not in text


def test_mobile_explicit_load_restores_full_editor_from_bottom_dock():
    """Opening a library document must remove its minimized dock chip and
    remount the editor instead of leaving the document tabbed along the bottom."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      const state = await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&mobile-library-open-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'mobile-library-doc',
          title: 'Mobile library document',
          language: 'richtext',
          current_content: '<p>Visible document</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
        mod.closePanel('down');
        await new Promise(resolve => setTimeout(resolve, 450));
        const minimized = {
          pane: Boolean(document.querySelector('#doc-editor-pane')),
          chip: Boolean(document.querySelector('[data-modal-id="doc-panel"]')),
        };
        mod.prepareDocumentOpen('mobile-session');
        await mod.loadDocument('mobile-library-doc');
        await new Promise(resolve => setTimeout(resolve, 100));
        return {
          minimized,
          restored: {
            pane: Boolean(document.querySelector('#doc-editor-pane')),
            chip: Boolean(document.querySelector('[data-modal-id="doc-panel"]')),
            docView: document.body.classList.contains('doc-view'),
            content: document.querySelector('#doc-email-richbody')?.innerText,
          },
          overflow: {
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          },
        };
      });
      console.log(JSON.stringify(state));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=SRC.parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["minimized"] == {"pane": False, "chip": True}
    assert data["restored"] == {
        "pane": True,
        "chip": False,
        "docView": True,
        "content": "Visible document",
    }
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]

"""Regression guards for restoring a chat's exact active document."""

from pathlib import Path
from tests.helpers.document_source import document_source, function_body


DOC_JS = document_source()


def test_active_document_is_persisted_per_session():
    assert "const _docActiveKey = (sessionId) => 'odysseus-doc-active-' + sessionId;" in DOC_JS
    assert "localStorage.setItem(_docActiveKey(sessionId), docId);" in DOC_JS
    assert "_rememberActiveDoc(docId);" in DOC_JS


def test_session_restore_prefers_exact_remembered_document():
    assert "const rememberedDocId = localStorage.getItem(_docActiveKey(sessionId));" in DOC_JS
    assert "activeDocs.find(doc => doc.id === rememberedDocId) || activeDocs[0]" in DOC_JS


def test_session_restore_rehydrates_existing_tab_content_without_http_cache():
    assert "cache: 'no-store'" in DOC_JS
    assert "for (const doc of activeDocs)" in DOC_JS
    assert "addDocToTabs(doc, sessionId);" in DOC_JS
    restore_loop = DOC_JS.split("for (const doc of activeDocs)", 1)[1].split("_syncDocIndicator", 1)[0]
    assert "if (!docs.has(doc.id))" not in restore_loop


def test_closing_active_document_clears_stale_restore_pointer():
    assert "_forgetActiveDoc(doc?.sessionId || _lastSessionId, docId);" in DOC_JS


def test_explicit_document_open_clears_minimized_dock_state():
    ensure_mounted = function_body("_ensureDocPaneMounted")

    assert "Modals.isMinimized('doc-panel')" in ensure_mounted
    assert "Modals.unregister('doc-panel');" in ensure_mounted
    assert "_markDocVisibleState(_lastSessionId, 'open');" in ensure_mounted


def test_library_open_intent_is_persisted_before_delayed_session_restore():
    body = function_body("prepareDocumentOpen")

    assert "_markDocVisibleState(sessionId, 'open');" in body
    assert "Modals.isMinimized('doc-panel')" in body
    assert "Modals.unregister('doc-panel');" in body
    assert "prepareDocumentOpen," in DOC_JS

"""Regression guards for in-chat document deep-links (#document-<id>).

The frontend module is browser-coupled (window/fetch/document) so there's
no JS unit harness for it — these pin the source-level invariants that the
404-silent-failure fix depends on. See issue #560.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_chat_document_links_use_the_document_id():
    """The list/open tool must anchor to the real document id, not a slug —
    a slug 404s against the UUID-keyed /api/document/<id> route."""
    src = (_REPO / "src" / "tool_implementations.py").read_text(encoding="utf-8")
    assert "(#document-{d.id})" in src
    assert "(#document-{doc.id})" in src


def test_document_deeplink_handled_on_hashchange_and_load():
    """#document-<id> in the URL must open the doc on refresh / URL-bar nav,
    not just on click."""
    js = (_REPO / "static" / "js" / "document.js").read_text(encoding="utf-8")
    assert "addEventListener('hashchange', _maybeOpenDocFromHash)" in js
    assert "#document-" in js


def test_failed_document_load_surfaces_user_error():
    """A missing/failed document must tell the user, not fail silently."""
    js = (_REPO / "static" / "js" / "document.js").read_text(encoding="utf-8")
    assert "uiModule.showError" in js
    assert "Document not found" in js


def test_document_close_clears_agent_active_context():
    """Closing a tab/panel must notify the backend so the next agent turn
    cannot rediscover the closed document as active context."""
    js = (_REPO / "static" / "js" / "document.js").read_text(encoding="utf-8")
    chat_js = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    routes = (_REPO / "routes" / "document_routes.py").read_text(encoding="utf-8")
    chat = (_REPO / "routes" / "chat_routes.py").read_text(encoding="utf-8")

    assert "/api/document/${encodeURIComponent(docId)}/close" in js
    assert "_clearServerActiveDocument(docId)" in js
    assert "_clearServerActiveDocument(closingDocId)" in js
    assert "fd.append('active_doc_state', 'open')" in chat_js
    assert "fd.append('active_doc_state', 'closed')" in chat_js
    assert '@router.post("/api/document/{doc_id}/close")' in routes
    assert "clear_active_document(doc_id)" in routes
    assert 'active_doc_state = form_data.get("active_doc_state", "").strip().lower()' in chat
    assert "active_doc_closed = active_doc_state == \"closed\"" in chat
    assert "get_closed_documents()" in chat
    assert "_session_doc_q.filter(~DBDocument.id.in_(_closed_doc_ids))" in chat

"""GET /api/documents/{session_id} must not N+1 on the folder relationship.

_doc_to_dict reads doc.folder for folder_name, and this endpoint serializes a
session's documents in a loop. Without eager loading, each filed doc triggers a
separate `SELECT ... FROM document_folders` (N+1). The endpoint uses
joinedload(Document.folder), so the folder comes back in the main query and no
standalone folder SELECT is emitted regardless of how many docs are filed.
"""
import pytest
from sqlalchemy import event

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_list_documents_eager_loads_folder(h):
    sid = h.seed_session("alice")
    fid = h.create_folder("alice", "Acme")["id"]
    for _ in range(3):
        h.seed_doc("alice", folder_id=fid, session_id=sid, title="filed")
    h.seed_doc("alice", folder_id=None, session_id=sid, title="loose")

    folder_selects = []

    @event.listens_for(h.engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        # The joinedload emits "... FROM documents LEFT OUTER JOIN
        # document_folders ..."; a lazy load emits "... FROM document_folders
        # WHERE ...". Only the latter contains "from document_folders".
        if "from document_folders" in statement.lower():
            folder_selects.append(statement)

    try:
        docs = h.list_session_docs("alice", sid)
    finally:
        event.remove(h.engine, "before_cursor_execute", _count)

    # No per-doc lazy folder load (would be 3 without joinedload).
    assert folder_selects == []

    by_title = {}
    for d in docs:
        by_title.setdefault(d["title"], []).append(d)
    assert all(d["folder_name"] == "Acme" for d in by_title["filed"])
    assert by_title["loose"][0]["folder_name"] is None
